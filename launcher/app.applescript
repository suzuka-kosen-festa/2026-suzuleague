-- スズリーグ.app の中身。ターミナルを開かずにダッシュボードを起動・終了する。
-- 処理は app.sh（この Mac）と jetson.sh（Jetson の遠隔操作）が行い、ここは画面（ダイアログ）だけを受け持つ。
-- 書き換えたら build-app.sh で .app を作り直す。
--
-- ダッシュボードはふだん Jetson で動かし、Jetson が使えないときはこの Mac で動かす。
-- 2台で同時に動かすと進行がぶつかるので、移すときは片方を止め、進行の保存を移してから、もう片方で続きから起動する。

property appTitle : "スズリーグ"
property localHostURL : "http://localhost:8000/host"
property phoneHostURL : "https://suzuleague-cloud.onrender.com/host.html"

on run
	set launcherDir to do shell script "dirname " & quoted form of POSIX path of (path to me)
	set sh to quoted form of (launcherDir & "/app.sh")
	set jsh to quoted form of (launcherDir & "/jetson.sh")
	try
		main(sh, jsh)
	on error errMsg number errNum
		if errNum is -128 then return -- キャンセルを押した
		display dialog errMsg buttons {"OK"} default button "OK" with icon stop with title appTitle
	end try
end run

on main(sh, jsh)
	set runState to do shell script sh & " status"
	if runState is "running" then
		askWhileRunning(sh, jsh)
		return
	end if
	if runState is "busy" then
		error "ポート8000が使われています。ターミナルで起動したダッシュボードが残っていれば、そちらで quit してから開き直してください。"
	end if

	set progress description to "Jetson の様子を確かめています…"
	set jetState to do shell script jsh & " status"
	if jetState is "running" then
		askWhileJetsonRunning(sh, jsh)
		return
	end if

	set onJetson to askPlace(jetState)
	if onJetson then
		set runner to jsh
	else
		set runner to sh
	end if

	set modeChoice to button returned of (display dialog "どちらで起動しますか？" buttons {"キャンセル", "本番", "デモ（1チーム5問）"} default button "デモ（1チーム5問）" cancel button "キャンセル" with title appTitle)
	if modeChoice is "本番" then
		set mode to "production"
	else
		set mode to "demo"
	end if
	set resumeArg to askResume(runner, mode)

	set progress total steps to 3
	set progress completed steps to 0
	set progress description to "Render を起こして、合言葉を確かめています（寝ていると数十秒かかります）…"
	checkToken(sh)
	checkOtherHost(sh)

	set progress completed steps to 1
	set progress description to "本番の画面が最新か確かめています…"
	try
		do shell script sh & " check-pages"
	on error errMsg
		display dialog "本番の画面が最新ではありません。古い画面のまま進めると表示がずれることがあります。" & return & "ステージ画面が違うときは、Scratch のプロジェクトが直されたか、チーム名が変わっています（作り直しが要ります）。" & return & return & errMsg buttons {"やめる", "このまま起動する"} default button "やめる" cancel button "やめる" with icon caution with title appTitle
	end try

	set progress completed steps to 2
	if onJetson then
		set progress description to "Jetson にコードを送って、ダッシュボードを起動しています（初回は数分かかります）…"
	else
		set progress description to "ダッシュボードを起動しています…"
	end if
	do shell script runner & " start " & mode & resumeArg
	set progress completed steps to 3

	openHostScreen(jsh, onJetson)
	if resumeArg is "" then
		set startedMsg to "起動しました（最初から）。"
	else
		set startedMsg to "起動しました（前回の続きから）。"
	end if
	display dialog placeName(onJetson) & "で" & startedMsg & return & return & "司会のスマホ: " & phoneHostURL & return & "このPC: ブラウザで開いた司会者画面" & return & return & "終えるときや移すときは、このアプリをもう一度開いてください。" buttons {"OK"} default button "OK" with title appTitle
end main

-- どこで動かすか聞く。Jetson なら true を返す
on askPlace(jetState)
	if jetState is "unreachable" then
		display dialog "Jetson につながりません（Jetson の電源と、Mac・Jetson の Tailscale を確認してください）。" & return & return & "司会者画面が「PCと接続中」なら、Jetson は動いています。このMacでは起動せず、キャンセルしてください。" & return & return & "Jetson が止まっているなら、このMacで起動できます。Jetson で動いていた進行は、数秒ごとにこのMacに写してあるので、「続きから」を選べば写せたところから続けられます。" buttons {"キャンセル", "このMacで起動"} default button "キャンセル" cancel button "キャンセル" with icon caution with title appTitle
		return false
	end if
	if jetState is "busy" then
		display dialog "Jetson のポート8000が使われています（ターミナルで起動したダッシュボードが残っているかもしれません）。" & return & return & "このMacで起動しますか？" buttons {"キャンセル", "このMacで起動"} default button "このMacで起動" cancel button "キャンセル" with icon caution with title appTitle
		return false
	end if
	set placeChoice to button returned of (display dialog "どこで動かしますか？" & return & return & "Jetson: ふだんはこちら" & return & "このMac: Jetson が使えないときの予備" buttons {"キャンセル", "このMac", "Jetson"} default button "Jetson" cancel button "キャンセル" with title appTitle)
	return placeChoice is "Jetson"
end askPlace

on placeName(onJetson)
	if onJetson then return "Jetson"
	return "このMac"
end placeName

-- 前回の進行が残っていれば、続きから始めるか聞く（裏方PCを起動し直したとき）。
-- runner は app.sh か jetson.sh。続きからなら " resume" を、最初からなら "" を返す
on askResume(runner, mode)
	set info to do shell script runner & " resume-info " & mode
	if info is "none" then return ""
	if info starts with "invalid:" then
		display dialog "前回の進行を読めませんでした（" & (text 9 thru -1 of info) & "）。最初から始めます。" buttons {"キャンセル", "OK"} default button "OK" cancel button "キャンセル" with icon caution with title appTitle
		return ""
	end if
	set choice to button returned of (display dialog "前回の続きがあります。" & return & return & info & return & return & "本番の開演前（リハーサルの後など）は「最初から」を選んでください。" buttons {"キャンセル", "最初から", "続きから"} default button "続きから" cancel button "キャンセル" with title appTitle)
	if choice is "続きから" then return " resume"
	display dialog "最初から始めます。前回の進行は消えます（念のため1つ前の分だけ残します）。" buttons {"キャンセル", "最初から始める"} default button "最初から始める" cancel button "キャンセル" with icon caution with title appTitle
	return ""
end askResume

-- 合言葉が Render と一致するまで聞き直す（Jetson には jetson.sh が起動のたびにこの Mac の合言葉を送る）
on checkToken(sh)
	repeat
		set tokenStatus to do shell script sh & " check-token"
		if tokenStatus is "ok" then return
		if tokenStatus is "unset" then error "Render に HOST_TOKEN が設定されていません。Render の管理画面の Environment で設定してください。"
		if tokenStatus starts with "unknown" then
			display dialog "合言葉を確かめられませんでした（" & tokenStatus & "）。ネットにつながっているか確認してください。" buttons {"やめる", "このまま続ける"} default button "やめる" cancel button "やめる" with icon caution with title appTitle
			return
		end if
		if tokenStatus is "wrong" then
			set msg to "合言葉が Render の HOST_TOKEN と違います。入れ直してください。"
		else
			set msg to "司会者画面の合言葉（Render の HOST_TOKEN と同じもの）を入力してください。次からは聞かれません。"
		end if
		set tokenText to text returned of (display dialog msg default answer "" with hidden answer buttons {"キャンセル", "OK"} default button "OK" cancel button "キャンセル" with title appTitle)
		if tokenText is not "" then
			-- 合言葉はコマンドの引数に載せず、標準入力で渡す
			do shell script "printf '%s\\n' " & quoted form of tokenText & " | " & sh & " save-token"
		end if
	end repeat
end checkToken

-- 別の場所（Jetson が SSH で届かないまま動いている、など）で裏方PCが動いていたら知らせる
on checkOtherHost(sh)
	set age to do shell script sh & " host-age"
	if age is "none" or age is "unknown" then return
	if (age as integer) < 10 then
		display dialog "別の裏方PCがまだ動いているようです（" & age & "秒前に、司会者画面の状態が届いています）。" & return & return & "このまま起動すると、2台で進行がぶつかります。もう一方を止めてから起動してください。" buttons {"やめる", "このまま起動する"} default button "やめる" cancel button "やめる" with icon caution with title appTitle
	end if
end checkOtherHost

-- この PC のブラウザで予備の司会者画面を開く（Jetson のときは SSH でこの Mac につなげてから開く）
on openHostScreen(jsh, onJetson)
	if onJetson then
		open location (do shell script jsh & " tunnel")
	else
		open location localHostURL
	end if
end openHostScreen

-- 移した先で起動する。保存が続きから始められるものなら続きから
on startAfterMove(runner, mode)
	set info to do shell script runner & " resume-info " & mode
	if info is "none" or info starts with "invalid:" then
		do shell script runner & " start " & mode
	else
		do shell script runner & " start " & mode & " resume"
	end if
end startAfterMove

on askWhileRunning(sh, jsh)
	set choice to button returned of (display dialog "スズリーグはこのMacで起動中です。" buttons {"終了する", "Jetsonに移す", "司会者画面を開く"} default button "司会者画面を開く" with title appTitle)
	if choice is "司会者画面を開く" then
		open location localHostURL
		return
	end if
	if choice is "Jetsonに移す" then
		moveToJetson(sh, jsh)
		return
	end if
	display dialog "本当に終了しますか？" & return & "進行中のゲームの状態（バルーンの数など）は消えます。" buttons {"キャンセル", "終了する"} default button "キャンセル" cancel button "キャンセル" with icon caution with title appTitle
	do shell script sh & " stop"
	display dialog "終了しました。" buttons {"OK"} default button "OK" with title appTitle
end askWhileRunning

on askWhileJetsonRunning(sh, jsh)
	-- この Mac を起動し直したなどで、進行の保存を写すのが止まっていれば始め直す
	try
		do shell script jsh & " mirror"
	end try
	set choice to button returned of (display dialog "スズリーグは Jetson で起動中です。" buttons {"終了する", "このMacに移す", "司会者画面を開く"} default button "司会者画面を開く" with title appTitle)
	if choice is "司会者画面を開く" then
		openHostScreen(jsh, true)
		return
	end if
	if choice is "このMacに移す" then
		moveToMac(sh, jsh)
		return
	end if
	display dialog "本当に Jetson のスズリーグを終了しますか？" & return & "進行中のゲームの状態（バルーンの数など）は消えます。" buttons {"キャンセル", "終了する"} default button "キャンセル" cancel button "キャンセル" with icon caution with title appTitle
	do shell script jsh & " stop"
	do shell script jsh & " untunnel"
	display dialog "Jetson のスズリーグを終了しました。" buttons {"OK"} default button "OK" with title appTitle
end askWhileJetsonRunning

-- Jetson を止め、進行の保存をこの Mac に持ってきて、続きから起動する
on moveToMac(sh, jsh)
	display dialog "Jetson を止めて、このMacで続きから動かします。" & return & return & "移し終えるまでの数十秒は、司会者画面の操作が効きません。" buttons {"キャンセル", "移す"} default button "移す" cancel button "キャンセル" with icon caution with title appTitle
	set mode to do shell script jsh & " mode"
	if mode is "none" then error "Jetson で動いているのがデモか本番かわかりませんでした。Jetson を終了してから、このMacで起動し直してください。"
	set progress description to "Jetson を止めて、進行の保存を持ってきています…"
	do shell script jsh & " stop"
	do shell script jsh & " untunnel"
	do shell script jsh & " pull-save " & mode
	set progress description to "このMacで続きから起動しています…"
	checkToken(sh)
	startAfterMove(sh, mode)
	open location localHostURL
	display dialog "このMacに移しました。司会のスマホはそのまま使えます。" buttons {"OK"} default button "OK" with title appTitle
end moveToMac

-- この Mac を止め、進行の保存を Jetson に送って、続きから起動する。Jetson で起動できなければこの Mac で起動し直す
on moveToJetson(sh, jsh)
	set jetState to do shell script jsh & " status"
	if jetState is "unreachable" then error "Jetson につながりません（Jetson の電源と、Mac・Jetson の Tailscale を確認してください）。このMacで動かしたままにします。"
	if jetState is "running" or jetState is "busy" then error "Jetson でもダッシュボードが動いています。先に Jetson のほうを止めてください。"
	display dialog "このMacを止めて、Jetson で続きから動かします。" & return & return & "移し終えるまでの数十秒は、司会者画面の操作が効きません。" buttons {"キャンセル", "移す"} default button "移す" cancel button "キャンセル" with icon caution with title appTitle
	set mode to do shell script sh & " mode"
	if mode is "none" then error "このMacで動いているのがデモか本番かわかりませんでした。このMacを終了してから、Jetson で起動し直してください。"
	-- 止める前にコードを送っておき、操作が効かない時間を短くする
	set progress description to "Jetson にコードを送っています（初回は数分かかります）…"
	do shell script jsh & " setup"
	set progress description to "このMacを止めて、進行の保存を Jetson に送っています…"
	do shell script sh & " stop"
	do shell script jsh & " push-save " & mode
	set progress description to "Jetson で続きから起動しています…"
	try
		startAfterMove(jsh, mode)
	on error errMsg
		display dialog "Jetson で起動できませんでした。このMacで続きから起動し直します。" & return & return & errMsg buttons {"OK"} default button "OK" with icon caution with title appTitle
		startAfterMove(sh, mode)
		open location localHostURL
		return
	end try
	openHostScreen(jsh, true)
	display dialog "Jetson に移しました。司会のスマホはそのまま使えます。" buttons {"OK"} default button "OK" with title appTitle
end moveToJetson
