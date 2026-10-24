// マニュアルの画面写真に、注釈（番号つきの枠）を重ねる。
// tools/manual_pdf.py が、撮った画像の場所（SHOT_BASE）と部品の位置（SHOTS）と QR コード（QR）を
// 先に window に入れてからこのページを開き、PDF にする。
//
//   <figure class="shot phone" data-shot="host-idle" data-crop="0,60">
//     <i class="pin" data-part="next">1</i>          … 司会者画面などの部品（tools/manual_shots.py の *_PARTS）
//     <i class="pin" data-sprite="決定">2</i>        … ステージ画面の部品（スプライト名。同じ名前が複数あればまとめて囲む）
//     <i class="pin" data-box="10,20,30,8">3</i>     … 位置を直接（左・上・幅・高さを画面に対する%で）
//   </figure>
//
// data-crop="上,下" は、画面の上から何%〜何%だけを見せるか。pin の data-at で番号の置き場所
// （left / right / top / bottom）、data-pad で枠の余白（%）、class="pin plain" で枠なしにできる。
(function () {
  "use strict";
  var SHOTS = window.SHOTS || {};
  var BASE = window.SHOT_BASE || "shots/";
  var pending = 0;

  function rectOf(pin, meta) {
    if (pin.dataset.box) return pin.dataset.box.split(",").map(Number);
    if (pin.dataset.part) return (meta.boxes || {})[pin.dataset.part] || null;
    if (pin.dataset.sprite) {
      var names = pin.dataset.sprite.split("|");
      var costume = pin.dataset.costume;
      var hits = (meta.sprites || []).filter(function (s) {
        return names.indexOf(s.name) >= 0 && (!costume || s.costume === costume);
      });
      if (!hits.length) return null;
      var x0 = Math.min.apply(null, hits.map(function (s) { return s.box[0]; }));
      var y0 = Math.min.apply(null, hits.map(function (s) { return s.box[1]; }));
      var x1 = Math.max.apply(null, hits.map(function (s) { return s.box[0] + s.box[2]; }));
      var y1 = Math.max.apply(null, hits.map(function (s) { return s.box[1] + s.box[3]; }));
      return [x0, y0, x1 - x0, y1 - y0];
    }
    return null;
  }

  function place(fig) {
    var name = fig.dataset.shot;
    var meta = SHOTS[name];
    var crop = (fig.dataset.crop || "0,100").split(",").map(Number);
    var span = crop[1] - crop[0];
    var frame = document.createElement("div");
    frame.className = "frame";
    var inner = document.createElement("div");
    inner.className = "inner";
    var img = document.createElement("img");
    pending++;
    img.onload = img.onerror = function () { pending--; if (img.naturalWidth === 0) fig.classList.add("missing"); };
    img.src = BASE + name + ".jpg";
    inner.appendChild(img);
    if (meta) {
      frame.style.aspectRatio = meta.w + " / " + (meta.h * span / 100);
      inner.style.height = (100 / span * 100) + "%";
      inner.style.top = (-crop[0] / span * 100) + "%";
    } else {
      fig.classList.add("missing");
      frame.style.aspectRatio = "1 / 1";
    }
    // ステージ画面の問題文は本番の問題の絵なので、いつも隠す（出演者に先に見せない）
    if (meta && meta.sprites && !fig.hasAttribute("data-show-question")) {
      meta.sprites.filter(function (s) { return s.name === "問題"; }).forEach(function (s) {
        var m = document.createElement("div");
        m.className = "mask";
        m.style.left = s.box[0] + "%"; m.style.top = s.box[1] + "%";
        m.style.width = s.box[2] + "%"; m.style.height = s.box[3] + "%";
        m.innerHTML = "<span>ここに問題文が出ます</span>";
        inner.appendChild(m);
      });
    }
    var pins = Array.prototype.slice.call(fig.querySelectorAll(":scope > .pin"));
    pins.forEach(function (pin) {
      var r = meta && rectOf(pin, meta);
      if (!r) { pin.classList.add("lost"); pin.title = "位置がわかりません"; inner.appendChild(pin); return; }
      var pad = Number(pin.dataset.pad || 0.8);
      var x = r[0] - pad, y = r[1] - pad * meta.w / meta.h, w = r[2] + pad * 2, h = r[3] + pad * 2 * meta.w / meta.h;
      if (!pin.classList.contains("plain")) {
        var hl = document.createElement("div");
        hl.className = "hl";
        hl.style.left = x + "%"; hl.style.top = y + "%"; hl.style.width = w + "%"; hl.style.height = h + "%";
        inner.appendChild(hl);
      }
      // 番号は枠の外側に置き、中の文字を隠さない（写真の端からははみ出さない）
      var at = pin.dataset.at || "left";
      var cx = at === "right" ? x + w : at === "left" ? x : x + w / 2;
      var cy = at === "top" ? y : at === "bottom" ? y + h : y + h / 2;
      if (pin.dataset.dx) cx += Number(pin.dataset.dx);
      if (pin.dataset.dy) cy += Number(pin.dataset.dy);
      var out = pin.classList.contains("plain") ? "0mm" : "2.6mm";
      var ox = at === "left" ? "- " + out : at === "right" ? "+ " + out : "+ 0mm";
      var oy = at === "top" ? "- " + out : at === "bottom" ? "+ " + out : "+ 0mm";
      pin.style.left = "min(calc(100% - 3.4mm), max(3.4mm, calc(" + cx + "% " + ox + ")))";
      pin.style.top = "min(calc(100% - 3.4mm), max(3.4mm, calc(" + cy + "% " + oy + ")))";
      inner.appendChild(pin);
    });
    frame.appendChild(inner);
    fig.insertBefore(frame, fig.firstChild);
  }

  Array.prototype.forEach.call(document.querySelectorAll("figure[data-shot]"), place);
  Array.prototype.forEach.call(document.querySelectorAll("[data-qr]"), function (el) {
    el.innerHTML = (window.QR || {})[el.dataset.qr] || "";
  });
  (function ready() {
    if (pending > 0) { setTimeout(ready, 50); return; }
    document.fonts.ready.then(function () { window.MANUAL_READY = true; });
  })();
})();
