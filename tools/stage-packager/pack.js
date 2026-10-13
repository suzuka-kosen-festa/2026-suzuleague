// sb3 を TurboWarp Packager で HTML（zip）に書き出す。
// 使い方: node pack.js <project.sb3> <出力.zip> <ルームID> <タイトル>
// src/suzuleague/stage.py から呼ばれる。素材は sb3 の中に入っている。
const fs = require('fs');
const Packager = require('@turbowarp/packager');

const [source, output, roomId, title] = process.argv.slice(2);

(async () => {
  const project = await Packager.loadProject(fs.readFileSync(source), () => {});
  const packager = new Packager.Packager();
  packager.project = project;
  const options = packager.options;
  options.target = 'zip'; // index.html と素材を別ファイルにする（素材が変わらなければ Git の差分が小さい）
  options.projectId = roomId; // クラウド変数のルームID
  options.cloudVariables.mode = 'ws';
  options.cloudVariables.cloudHost = 'wss://stage.invalid'; // stage.py が「配信元のサーバ」に書き換える
  options.app.windowTitle = title;
  options.autoplay = false; // 最初に1回クリックしてもらう（クリックがないとブラウザが音を出さない）
  options.controls.fullscreen.enabled = true;
  const result = await packager.package();
  fs.writeFileSync(output, result.data);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
