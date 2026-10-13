"""ステージ画面の作り直しのテスト（ネットワーク・Node 不要）。

本物の Scratch プロジェクトの代わりに、頼っているメッセージと変数だけを持つ小さなプロジェクトで確かめる。
実物での動きは、書き出したものを手元のサーバにつないでブラウザで確かめる（docs/development.md）。
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from suzuleague import stage, stage_result


def _hat(message_id: str, message: str, next_id: str | None = None) -> dict:
    return {
        "opcode": "event_whenbroadcastreceived",
        "next": next_id,
        "parent": None,
        "inputs": {},
        "fields": {"BROADCAST_OPTION": [message, message_id]},
        "shadow": False,
        "topLevel": True,
        "x": 0,
        "y": 0,
    }


def _sprite(name: str, blocks: dict | None = None) -> dict:
    return {
        "isStage": False,
        "name": name,
        "variables": {},
        "lists": {},
        "broadcasts": {},
        "blocks": blocks or {},
        "comments": {},
        "costumes": [],
        "sounds": [],
        "visible": True,
    }


def make_project() -> dict:
    """元のプロジェクトのうち、作り直しが頼っている部分だけを持つもの。"""
    messages = [*stage.REQUIRED_MESSAGES, "回答_確定", "7_エキシビジョン回答受付"]
    broadcasts = {f"m{i}": name for i, name in enumerate(messages)}
    msg_id = {name: i for i, name in broadcasts.items()}

    variables = {f"v{i}": [name, "9"] for i, name in enumerate(stage.REQUIRED_VARIABLES)}
    for i, name in enumerate(("SEQ", "STATE", "TEAM", "ROUND", "QID", "CORRECT", "BALLOONS")):
        variables[f"c{i}"] = [f"☁ P2S_{name}", "4", True]  # 保存したときの値が残っている
    variables |= {
        "s0": ["☁ S2P_ANSWER", 45, True],
        "s1": ["☁ S2P_SEQ", 29, True],
        "s2": ["☁ S2P_ACK", "1", True],
    }
    stage_target = _sprite("Stage") | {
        "isStage": True,
        "variables": variables,
        "lists": {"l0": ["★入力した文字", []], "l1": ["得点表", []]},
        "broadcasts": broadcasts,
    }

    # 演出: すべてのメッセージを受け取る。登場の演出は最後に 回答中 を 0.1 にする
    show = {f"hat{i}": _hat(msg_id[name], name) for i, name in enumerate(stage.REQUIRED_MESSAGES)}
    intro = next(k for k, b in show.items() if b["fields"]["BROADCAST_OPTION"][0] == "演出_風船登場開始")
    show[intro]["next"] = "start"
    show["start"] = {
        "opcode": "event_broadcast",
        "next": "back",
        "parent": intro,
        "inputs": {"BROADCAST_INPUT": [1, [11, "初期化", msg_id["初期化"]]]},
        "fields": {},
        "shadow": False,
        "topLevel": False,
    }
    show["back"] = {
        "opcode": "data_setvariableto",
        "next": None,
        "parent": "start",
        "inputs": {"VALUE": [1, [10, "0.1"]]},
        "fields": {"VARIABLE": ["回答中", "v0"]},
        "shadow": False,
        "topLevel": False,
    }

    # 通信: 元のスクリプト。回答を S2P_ANSWER に書く
    old = {
        "old-hat": _hat(msg_id["回答_確定"], "回答_確定", "old-set"),
        "old-set": {
            "opcode": "data_setvariableto",
            "next": None,
            "parent": "old-hat",
            "inputs": {"VALUE": [1, [10, "1"]]},
            "fields": {"VARIABLE": ["☁ S2P_ACK", "s2"]},
            "shadow": False,
            "topLevel": False,
        },
    }
    flag = {"opcode": "event_whenflagclicked", "next": None, "parent": None, "inputs": {}, "fields": {}, "shadow": False, "topLevel": True}
    targets = [stage_target, _sprite("演出", show), _sprite("通信", old), _sprite("問題")]
    targets += [_sprite(name, {"flag": dict(flag)}) for name in stage.INPUT_SPRITES]
    return {"targets": targets, "monitors": [], "extensions": [], "meta": {"semver": "3.0.0"}}


def all_blocks(project: dict):
    for target in project["targets"]:
        for block_id, block in target["blocks"].items():
            yield target, block_id, block


def writes_to(project: dict, prefix: str) -> list[str]:
    """prefix で始まる変数に書き込むブロックのあるスプライト。"""
    return [
        target["name"]
        for target, _, block in all_blocks(project)
        if block["opcode"] in ("data_setvariableto", "data_changevariableby")
        and block["fields"]["VARIABLE"][0].startswith(prefix)
    ]


def stage_variables(project: dict) -> dict[str, list]:
    return {v[0]: v for v in project["targets"][0]["variables"].values()}


@pytest.fixture(params=list(stage.VARIANTS))
def variant(request) -> str:
    return request.param


class TestTransform:
    def test_view_cannot_send_answers(self):
        """大画面用が S2P_* を書くと、出演者の回答として採点されてしまう。"""
        made, _ = stage.transform(make_project(), "view")
        assert writes_to(made, "☁ S2P_") == [] and writes_to(made, "S2P_") == []
        variables = stage_variables(made)
        # Packager は ☁ で始まる変数をすべてクラウド変数にするので、名前ごと外してあること
        assert not [name for name in variables if name.startswith("☁ S2P_")]
        for name in ("S2P_ANSWER", "S2P_SEQ", "S2P_ACK"):
            assert len(variables[name]) == 2, f"{name} が送れる変数のまま"
        for name in stage.INPUT_SPRITES:
            target = stage.find_target(made, name)
            assert target["blocks"] == {} and target["visible"] is False

    def test_play_sends_the_answer_when_confirmed(self):
        made, _ = stage.transform(make_project(), "play")
        target = stage.find_target(made, "通信")
        (hat,) = [b for b in target["blocks"].values() if b["opcode"] == "event_whenbroadcastreceived"]
        assert hat["fields"]["BROADCAST_OPTION"][0] == "回答_確定"
        first = target["blocks"][hat["next"]]
        second = target["blocks"][first["next"]]
        # ANSWER → SEQ の順。裏方PCは SEQ ではなく ANSWER を見ているが、仕様の順序を守る
        assert (first["opcode"], first["fields"]["VARIABLE"][0]) == ("data_setvariableto", "☁ S2P_ANSWER")
        assert (second["opcode"], second["fields"]["VARIABLE"][0]) == ("data_changevariableby", "☁ S2P_SEQ")
        variables = stage_variables(made)
        assert variables["☁ S2P_ANSWER"][2] is True and variables["☁ S2P_SEQ"][2] is True
        assert "☁ S2P_ACK" not in variables and len(variables["S2P_ACK"]) == 2  # 使わない
        assert stage.find_target(made, "キー")["blocks"] != {}

    def test_never_writes_to_p2s(self, variant):
        made, _ = stage.transform(make_project(), variant)
        assert writes_to(made, "☁ P2S_") == []

    def test_cloud_variables_start_from_idle(self, variant):
        """保存したときの値が残っていると、裏方PCより先に開いたときに途中の画面を出してしまう。"""
        made, _ = stage.transform(make_project(), variant)
        variables = stage_variables(made)
        for name, default in stage.CLOUD_DEFAULTS.items():
            if name in variables:  # 大画面用には S2P_* がない
                assert variables[name][1] == default
        for name in ("☁ P2S_STATE", "☁ P2S_ANSWER", "☁ P2S_SCORES"):
            assert variables[name][2] is True

    def test_only_intended_variables_are_cloud(self, variant):
        made, _ = stage.transform(make_project(), variant)
        cloud = {name for name in stage_variables(made) if name.startswith("☁ ")}
        expected = {n for n in stage.CLOUD_DEFAULTS if n.startswith("☁ P2S_") or variant == "play"}
        assert cloud == expected

    def test_renamed_variables_keep_their_references(self):
        """名前を変えた変数を使うブロックとモニターが、古い名前のまま残らない。"""
        source = make_project()
        blocks = stage.find_target(source, "演出")["blocks"]
        blocks["reader"] = {
            "opcode": "data_setvariableto",
            "next": None,
            "parent": None,
            "inputs": {"VALUE": [3, [12, "☁ S2P_ACK", "s2"], [10, ""]]},
            "fields": {"VARIABLE": ["回答中", "v0"]},
            "shadow": False,
            "topLevel": True,
        }
        source["monitors"] = [{"id": "s2", "opcode": "data_variable", "params": {"VARIABLE": "☁ S2P_ACK"}}]
        made, _ = stage.transform(source, "play")
        reader = stage.find_target(made, "演出")["blocks"]["reader"]
        assert reader["inputs"]["VALUE"][1][1] == "S2P_ACK"
        assert made["monitors"][0]["params"]["VARIABLE"] == "S2P_ACK"

    def test_original_communication_scripts_are_gone(self, variant):
        made, _ = stage.transform(make_project(), variant)
        assert "old-hat" not in stage.find_target(made, "通信")["blocks"]

    def test_blocks_are_well_formed(self, variant):
        """ID のつながりが壊れていると、読み込みには成功して黙って動かない。"""
        made, _ = stage.transform(make_project(), variant)
        stage_target = made["targets"][0]
        for target, block_id, block in all_blocks(made):
            blocks = target["blocks"]
            where = f"{target['name']} {block_id} {block['opcode']}"
            if block["next"]:
                assert blocks[block["next"]]["parent"] == block_id, where
            if block["topLevel"]:
                assert block["parent"] is None, where
            else:
                assert block["parent"] in blocks, where
            for value in block["inputs"].values():
                inner = value[1]
                if isinstance(inner, str):
                    assert blocks[inner]["parent"] == block_id, where
                elif inner[0] == 12:  # 変数
                    owner = target if inner[2] in target["variables"] else stage_target
                    assert owner["variables"][inner[2]][0] == inner[1], where
                elif inner[0] == 11:  # メッセージ
                    assert stage_target["broadcasts"][inner[2]] == inner[1], where
            for name, value in block["fields"].items():
                if name in ("VARIABLE", "LIST"):
                    kind = "variables" if name == "VARIABLE" else "lists"
                    owner = target if value[1] in target[kind] else stage_target
                    assert owner[kind][value[1]][0] == value[0], where
                elif name == "BROADCAST_OPTION":
                    assert stage_target["broadcasts"][value[1]] == value[0], where

    def test_called_blocks_are_defined(self, variant):
        made, _ = stage.transform(make_project(), variant)
        blocks = stage.find_target(made, "通信")["blocks"].values()
        defined = {b["mutation"]["proccode"] for b in blocks if b["opcode"] == "procedures_prototype"}
        called = {b["mutation"]["proccode"] for b in blocks if b["opcode"] == "procedures_call"}
        assert called and called <= defined

    def test_source_is_left_untouched(self, variant):
        source = make_project()
        before = json.dumps(source, sort_keys=True, ensure_ascii=False)
        stage.transform(source, variant)
        assert json.dumps(source, sort_keys=True, ensure_ascii=False) == before

    def test_digest_is_stable_and_differs_by_variant(self):
        view_a, _ = stage.transform(make_project(), "view")
        view_b, _ = stage.transform(make_project(), "view")
        play, _ = stage.transform(make_project(), "play")
        assert stage.digest(view_a) == stage.digest(view_b)
        assert stage.digest(view_a) != stage.digest(play)

    def test_unknown_variant(self):
        with pytest.raises(ValueError):
            stage.transform(make_project(), "audience")


class TestResultScreen:
    """全体結果は、元の得点表ではなく、ここで足したスプライトが出す。"""

    def test_sprite_uses_only_bundled_assets(self, variant):
        made, _ = stage.transform(make_project(), variant)
        sprite = stage.find_target(made, stage_result.SPRITE)
        bundled = stage_result.assets()
        assert sprite["visible"] is False
        assert {c["md5ext"] for c in sprite["costumes"]} == set(bundled)
        names = [c["name"] for c in sprite["costumes"]]
        assert len(names) == len(set(names))
        for needed in ("背景", "カード1", "カード5", "数0", "数9", "順位1", "ゲームオーバー", "優勝", "金枠"):
            assert needed in names

    def test_costumes_are_valid_svg(self):
        from xml.etree import ElementTree

        for costume in stage_result.costumes():
            root = ElementTree.fromstring(costume.svg)
            assert root.tag.endswith("svg"), costume.name
            assert costume.center == (float(root.get("width")) / 2, float(root.get("height")) / 2)

    def test_final_state_shows_our_screen_not_the_list(self, variant):
        made, _ = stage.transform(make_project(), variant)
        blocks = stage.find_target(made, "通信")["blocks"].values()
        sent = {b["inputs"]["BROADCAST_INPUT"][1][1] for b in blocks if b["opcode"] == "event_broadcast"}
        assert stage_result.SHOW_MESSAGE in sent
        assert "8_終了" not in sent  # 元の得点表（リストが見出しに重なる）は出さない
        assert not [b for b in blocks if b["opcode"] == "data_showlist"]

    def test_existing_sprite_with_the_same_name_is_an_error(self):
        source = make_project()
        source["targets"].append(_sprite(stage_result.SPRITE))
        with pytest.raises(stage.ProjectError):
            stage.transform(source, "view")


class TestBuildSb3:
    def test_bundles_project_original_and_added_assets(self):
        made, _ = stage.transform(make_project(), "view")
        made["targets"][0]["costumes"] = [{"name": "空", "md5ext": "aaa.svg"}]
        made["targets"][0]["sounds"] = [{"name": "音", "md5ext": "bbb.wav"}]
        fetched = []

        def fetch(md5ext: str) -> bytes:
            fetched.append(md5ext)
            return b"original:" + md5ext.encode()

        packed = stage.build_sb3(made, stage_result.assets(), fetch)
        with zipfile.ZipFile(io.BytesIO(packed)) as archive:
            names = set(archive.namelist())
            assert json.loads(archive.read("project.json"))["targets"][0]["name"] == "Stage"
            assert archive.read("aaa.svg") == b"original:aaa.svg"
            assert archive.read("bbb.wav") == b"original:bbb.wav"
            for md5ext, data in stage_result.assets().items():
                assert archive.read(md5ext) == data
        assert sorted(fetched) == ["aaa.svg", "bbb.wav"]  # 足した素材は取りに行かない
        assert names == {"project.json", "aaa.svg", "bbb.wav", *stage_result.assets()}


class TestFirstQuestionKeypad:
    def test_reset_is_guarded(self, variant):
        """登場の効果音のあとで 回答中 を戻すのは、まだ 0 のときだけ（#53）。"""
        made, notes = stage.transform(make_project(), variant)
        assert notes == []
        blocks = stage.find_target(made, "演出")["blocks"]
        guard = blocks[blocks["back"]["parent"]]
        assert guard["opcode"] == "control_if"
        assert guard["inputs"]["SUBSTACK"] == [2, "back"]
        assert blocks["start"]["next"] == blocks["back"]["parent"]
        condition = blocks[guard["inputs"]["CONDITION"][1]]
        assert condition["opcode"] == "operator_equals"
        assert condition["inputs"]["OPERAND1"][1][1] == "回答中"
        assert condition["inputs"]["OPERAND2"][1][1] == "0"

    def test_missing_reset_is_reported_not_fatal(self, variant):
        """Scratch 側で直されていたら、当てなかったと知らせて先へ進む。"""
        source = make_project()
        blocks = stage.find_target(source, "演出")["blocks"]
        del blocks["back"]
        blocks["start"]["next"] = None
        _, notes = stage.transform(source, variant)
        assert len(notes) == 1 and "#53" in notes[0]


class TestCheckSource:
    def test_missing_receiver_is_an_error(self):
        source = make_project()
        blocks = stage.find_target(source, "演出")["blocks"]
        gone = next(k for k, b in blocks.items() if b["fields"].get("BROADCAST_OPTION", [""])[0] == "4_正解発表")
        del blocks[gone]
        with pytest.raises(stage.StageError, match="4_正解発表"):
            stage.transform(source, "view")

    def test_missing_variable_and_sprite_are_errors(self):
        source = make_project()
        del source["targets"][0]["variables"]["v0"]
        source["targets"] = [t for t in source["targets"] if t["name"] != "決定"]
        with pytest.raises(stage.StageError) as e:
            stage.transform(source, "play")
        assert "回答中" in str(e.value) and "決定" in str(e.value)


def packed_zip(index: str) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("index.html", index)
        archive.writestr("assets/project.json", "{}")
    return data.getvalue()


class TestWriteVariant:
    def test_connects_to_the_server_that_serves_the_page(self, tmp_path):
        """接続先を決め打ちにすると、手元のサーバや代替手段で動かせない。"""
        index = f'new WebSocketProvider("{stage.CLOUD_HOST_PLACEHOLDER}", "room")'
        out = tmp_path / "stage" / "view"
        stage.write_variant(packed_zip(index), out, {"digest": "abc"})
        html = (out / "index.html").read_text(encoding="utf-8")
        assert stage.CLOUD_HOST_PLACEHOLDER not in html
        assert "location.host" in html
        assert (out / "assets" / "project.json").exists()
        assert json.loads((out / "version.json").read_text(encoding="utf-8")) == {"digest": "abc"}

    def test_old_files_are_removed(self, tmp_path):
        out = tmp_path / "view"
        (out / "assets").mkdir(parents=True)
        (out / "assets" / "old.svg").write_text("x")
        stage.write_variant(packed_zip(f'"{stage.CLOUD_HOST_PLACEHOLDER}"'), out, {})
        assert not (out / "assets" / "old.svg").exists()

    def test_unexpected_packager_output_is_an_error(self, tmp_path):
        with pytest.raises(stage.StageError):
            stage.write_variant(packed_zip("<html></html>"), tmp_path / "view", {})


class TestCheck:
    def _patch(self, monkeypatch, live: dict[str, dict]):
        monkeypatch.setattr(stage, "fetch_project", lambda project_id: (make_project(), "2026-10-12"))
        monkeypatch.setattr(stage, "_get_json", lambda url: live[url.split("/")[-2]])

    def _version(self, variant: str, **changes) -> dict:
        made, _ = stage.transform(make_project(), variant)
        return {"room": "room", "digest": stage.digest(made), "source_modified": "2026-10-12"} | changes

    def test_matches(self, monkeypatch):
        self._patch(monkeypatch, {v: self._version(v) for v in stage.VARIANTS})
        assert stage.check("https://example.invalid", "room") == []

    def test_source_updated_on_scratch(self, monkeypatch):
        live = {v: self._version(v) for v in stage.VARIANTS}
        live["view"] = self._version("view", digest="old", source_modified="2026-10-01")
        self._patch(monkeypatch, live)
        (problem,) = stage.check("https://example.invalid", "room")
        assert "大画面用" in problem and "Scratch 側が更新" in problem

    def test_wrong_room(self, monkeypatch):
        self._patch(monkeypatch, {v: self._version(v, room="suzuleague-dev") for v in stage.VARIANTS})
        assert len(stage.check("https://example.invalid", "room")) == 2
