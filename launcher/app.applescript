-- スズリーグ.app の中身。ターミナルを開かずにダッシュボードを起動・終了する。
-- 処理は app.sh が行い、ここは画面（ダイアログ）だけを受け持つ。
-- 書き換えたら build-app.sh で .app を作り直す。

property appTitle : "スズリーグ"
property localHostURL : "http://localhost:8000/host"
property phoneHostURL : "https://suzuleague-cloud.onrender.com/host.html"

on run
	set launcherDir to do shell script "dirname " & quoted form of POSIX path of (path to me)
	set sh to quoted form of (launcherDir & "/app.sh")
	try
		main(sh)
	on error errMsg number errNum
		if errNum is -128 then return -- キャンセルを押した
		display dialog errMsg buttons {"OK"} default button "OK" with icon stop with title appTitle
	end try
end run

on main(sh)
	set runState to do shell script sh & " status"
	if runState is "running" then
		askWhileRunning(sh)
		return
	end if
	if runState is "busy" then
		error "ポート8000が使われています。ターミナルで起動したダッシュボードが残っていれば、そちらで quit してから開き直してください。"
	end if

	set modeChoice to button returned of (display dialog "どちらで起動しますか？" buttons {"キャンセル", "本番", "デモ（1チーム5問）"} default button "デモ（1チーム5問）" cancel button "キャンセル" with title appTitle)
	if modeChoice is "本番" then
		set mode to "production"
	else
		set mode to "demo"
	end if
	set resumeArg to askResume(sh, mode)

	set progress total steps to 3
	set progress completed steps to 0
	set progress description to "Render を起こして、合言葉を確かめています（寝ていると数十秒かかります）…"
	checkToken(sh)

	set progress completed steps to 1
	set progress description to "本番の画面が最新か確かめています…"
	try
		do shell script sh & " check-pages"
	on error errMsg
		display dialog "本番の画面が手元と違います。古い画面のまま進めると表示がずれることがあります。" & return & return & errMsg buttons {"やめる", "このまま起動する"} default button "やめる" cancel button "やめる" with icon caution with title appTitle
	end try

	set progress completed steps to 2
	set progress description to "ダッシュボードを起動しています…"
	do shell script sh & " start " & mode & resumeArg
	set progress completed steps to 3

	open location localHostURL
	if resumeArg is "" then
		set startedMsg to "起動しました（最初から）。"
	else
		set startedMsg to "起動しました（前回の続きから）。"
	end if
	display dialog startedMsg & return & return & "司会のスマホ: " & phoneHostURL & return & "このPC: ブラウザで開いた司会者画面" & return & return & "終えるときは、このアプリをもう一度開いてください。" buttons {"OK"} default button "OK" with title appTitle
end main

-- 前回の進行が残っていれば、続きから始めるか聞く（裏方PCを起動し直したとき）。
-- 続きからなら " resume" を、最初からなら "" を返す
on askResume(sh, mode)
	set info to do shell script sh & " resume-info " & mode
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

-- 合言葉が Render と一致するまで聞き直す
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

on askWhileRunning(sh)
	set choice to button returned of (display dialog "スズリーグは起動中です。" buttons {"終了する", "司会者画面を開く"} default button "司会者画面を開く" with title appTitle)
	if choice is "司会者画面を開く" then
		open location localHostURL
		return
	end if
	display dialog "本当に終了しますか？" & return & "進行中のゲームの状態（バルーンの数など）は消えます。" buttons {"キャンセル", "終了する"} default button "キャンセル" cancel button "キャンセル" with icon caution with title appTitle
	do shell script sh & " stop"
	display dialog "終了しました。" buttons {"OK"} default button "OK" with title appTitle
end askWhileRunning
