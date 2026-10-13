"""Scratch のプロジェクト（sb3 の project.json）を読み書きする道具。

project.json のブロックは ID でつながった辞書なので、手で書くと読めない。
ここでは入れ子の Python の式で書き（`when(eq(Var("回答中"), 1), broadcast("初期化"))`）、
Scripts がまとめて辞書に直してスプライトに足す。

Scratch のステージ画面を作り直す stage.py から使う。スズリーグ固有の知識（どのメッセージを
どの順で送るか）はここには置かない。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


class ProjectError(Exception):
    """プロジェクトに、あるはずのスプライト・変数・リストがない。"""


# ---- ブロックを式で書く ----------------------------------------------


@dataclass(frozen=True)
class Var:
    name: str


@dataclass(frozen=True)
class Msg:
    name: str


@dataclass(frozen=True)
class Menu:
    """ドロップダウンで選ぶ値（背景の名前など）。"""

    opcode: str
    field: str
    value: str


@dataclass
class Block:
    opcode: str
    inputs: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, Any] = field(default_factory=dict)
    stacks: dict[str, list[Block]] = field(default_factory=dict)
    mutation: dict[str, Any] | None = None


# 真偽値を受ける入力（六角形の穴）。影になる値を持たない
BOOL_INPUTS = {
    ("control_if", "CONDITION"),
    ("control_if_else", "CONDITION"),
    ("operator_and", "OPERAND1"),
    ("operator_and", "OPERAND2"),
    ("operator_or", "OPERAND1"),
    ("operator_or", "OPERAND2"),
    ("operator_not", "OPERAND"),
}


def eq(a: Any, b: Any) -> Block:
    return Block("operator_equals", {"OPERAND1": a, "OPERAND2": b})


def gt(a: Any, b: Any) -> Block:
    return Block("operator_gt", {"OPERAND1": a, "OPERAND2": b})


def lt(a: Any, b: Any) -> Block:
    return Block("operator_lt", {"OPERAND1": a, "OPERAND2": b})


def both(a: Block, b: Block) -> Block:
    return Block("operator_and", {"OPERAND1": a, "OPERAND2": b})


def either(a: Block, b: Block) -> Block:
    return Block("operator_or", {"OPERAND1": a, "OPERAND2": b})


def no(a: Block) -> Block:
    return Block("operator_not", {"OPERAND": a})


def plus(a: Any, b: Any) -> Block:
    return Block("operator_add", {"NUM1": a, "NUM2": b})


def minus(a: Any, b: Any) -> Block:
    return Block("operator_subtract", {"NUM1": a, "NUM2": b})


def divide(a: Any, b: Any) -> Block:
    return Block("operator_divide", {"NUM1": a, "NUM2": b})


def times(a: Any, b: Any) -> Block:
    return Block("operator_multiply", {"NUM1": a, "NUM2": b})


def absolute(a: Any) -> Block:
    return Block("operator_mathop", {"NUM": a}, {"OPERATOR": "abs"})


def join(a: Any, b: Any) -> Block:
    return Block("operator_join", {"STRING1": a, "STRING2": b})


def letter(index: Any, text: Any) -> Block:
    return Block("operator_letter_of", {"LETTER": index, "STRING": text})


def length(text: Any) -> Block:
    return Block("operator_length", {"STRING": text})


def set_var(name: str, value: Any) -> Block:
    return Block("data_setvariableto", {"VALUE": value}, {"VARIABLE": Var(name)})


def change_var(name: str, by: Any) -> Block:
    return Block("data_changevariableby", {"VALUE": by}, {"VARIABLE": Var(name)})


def when(cond: Block, *body: Block) -> Block:
    return Block("control_if", {"CONDITION": cond}, stacks={"SUBSTACK": list(body)})


def when_else(cond: Block, body: list[Block], otherwise: list[Block]) -> Block:
    return Block("control_if_else", {"CONDITION": cond}, stacks={"SUBSTACK": body, "SUBSTACK2": otherwise})


def forever(*body: Block) -> Block:
    return Block("control_forever", stacks={"SUBSTACK": list(body)})


def repeat(times: Any, *body: Block) -> Block:
    return Block("control_repeat", {"TIMES": times}, stacks={"SUBSTACK": list(body)})


def wait(seconds: float) -> Block:
    return Block("control_wait", {"DURATION": seconds})


def broadcast(name: str) -> Block:
    return Block("event_broadcast", {"BROADCAST_INPUT": Msg(name)})


def call(name: str) -> Block:
    return Block("procedures_call", mutation=_procedure_mutation(name))


def delete_all(list_name: str) -> Block:
    return Block("data_deletealloflist", fields={"LIST": Var(list_name)})


def add_to(list_name: str, item: Any) -> Block:
    return Block("data_addtolist", {"ITEM": item}, {"LIST": Var(list_name)})


def item(index: Any, list_name: str) -> Block:
    return Block("data_itemoflist", {"INDEX": index}, {"LIST": Var(list_name)})


def list_length(list_name: str) -> Block:
    return Block("data_lengthoflist", fields={"LIST": Var(list_name)})


def show_list(list_name: str) -> Block:
    return Block("data_showlist", fields={"LIST": Var(list_name)})


def hide_list(list_name: str) -> Block:
    return Block("data_hidelist", fields={"LIST": Var(list_name)})


def stop_other_scripts() -> Block:
    return Block(
        "control_stop",
        fields={"STOP_OPTION": "other scripts in sprite"},
        mutation={"tagName": "mutation", "children": [], "hasnext": "true"},
    )


def create_clone() -> Block:
    """自分自身のクローンを作る。"""
    menu = Menu("control_create_clone_of_menu", "CLONE_OPTION", "_myself_")
    return Block("control_create_clone_of", {"CLONE_OPTION": menu})


def on_clone_start() -> Block:
    return Block("control_start_as_clone")


def on_flag() -> Block:
    return Block("event_whenflagclicked")


def on_message(name: str) -> Block:
    return Block("event_whenbroadcastreceived", fields={"BROADCAST_OPTION": Msg(name)})


def _procedure_mutation(name: str, *, prototype: bool = False) -> dict[str, Any]:
    mutation = {"tagName": "mutation", "children": [], "proccode": name, "argumentids": "[]", "warp": "false"}
    if prototype:
        mutation |= {"argumentnames": "[]", "argumentdefaults": "[]"}
    return mutation


class Scripts:
    """1つのスプライト（かステージ）にスクリプトを足す。"""

    PREFIX = "sz"  # 足したブロック・変数の ID の頭。元の ID とぶつからないようにする

    def __init__(self, project: dict[str, Any], name: str) -> None:
        self.stage = stage_of(project)
        self.target = self.stage if name == "Stage" else find_target(project, name)
        self.blocks: dict[str, Any] = self.target["blocks"]
        self._tops = 0

    def _new_id(self, kind: str, pool: dict[str, Any]) -> str:
        n = len(pool)
        while f"{self.PREFIX}-{kind}-{n}" in pool:
            n += 1
        return f"{self.PREFIX}-{kind}-{n}"

    # -- 変数・リスト・メッセージの参照 --

    def _lookup(self, kind: str, name: str) -> list[str]:
        for target in (self.target, self.stage):
            for item_id, item in target[kind].items():
                if item[0] == name:
                    return [name, item_id]
        what = "変数" if kind == "variables" else "リスト"
        raise ProjectError(f"{what}「{name}」が元のプロジェクトにありません")

    def variable(self, name: str) -> list[str]:
        try:
            return self._lookup("variables", name)
        except ProjectError:
            return self._lookup("lists", name)

    def message(self, name: str) -> list[str]:
        for msg_id, msg_name in self.stage["broadcasts"].items():
            if msg_name == name:
                return [name, msg_id]
        msg_id = self._new_id("msg", self.stage["broadcasts"])
        self.stage["broadcasts"][msg_id] = name
        return [name, msg_id]

    def add_variable(self, name: str, value: str = "0") -> None:
        """このスプライトだけの変数を足す（すでにあれば何もしない）。"""
        if any(v[0] == name for v in self.target["variables"].values()):
            return
        self.target["variables"][self._new_id("var", self.target["variables"])] = [name, value]

    # -- ブロックを辞書へ --

    def _put(self, block: Block, parent: str | None, *, top: bool = False) -> str:
        block_id = self._new_id("b", self.blocks)
        entry: dict[str, Any] = {
            "opcode": block.opcode,
            "next": None,
            "parent": parent,
            "inputs": {},
            "fields": {},
            "shadow": False,
            "topLevel": top,
        }
        if top:
            self._tops += 1
            entry |= {"x": 4000, "y": 400 * self._tops}  # 元のスクリプトと重ならない場所に置く
        self.blocks[block_id] = entry
        for name, value in block.inputs.items():
            entry["inputs"][name] = self._input(block.opcode, name, value, block_id)
        for name, value in block.fields.items():
            if isinstance(value, Var):
                entry["fields"][name] = self.variable(value.name)
            elif isinstance(value, Msg):
                entry["fields"][name] = self.message(value.name)
            else:
                entry["fields"][name] = [value, None]
        for name, stack in block.stacks.items():
            first = self._put_stack(stack, block_id)
            if first:
                entry["inputs"][name] = [2, first]
        if block.mutation:
            entry["mutation"] = block.mutation
        return block_id

    def _input(self, opcode: str, name: str, value: Any, parent: str) -> list[Any]:
        if isinstance(value, Block):
            child = self._put(value, parent)
            return [2, child] if (opcode, name) in BOOL_INPUTS else [3, child, [10, ""]]
        if isinstance(value, Var):
            return [3, [12, *self.variable(value.name)], [10, ""]]
        if isinstance(value, Msg):
            return [1, [11, *self.message(value.name)]]
        if isinstance(value, Menu):
            menu_id = self._new_id("b", self.blocks)
            self.blocks[menu_id] = {
                "opcode": value.opcode,
                "next": None,
                "parent": parent,
                "inputs": {},
                "fields": {value.field: [value.value, None]},
                "shadow": True,
                "topLevel": False,
            }
            return [1, menu_id]
        return [1, [10, str(value)]]

    def _put_stack(self, stack: list[Block], parent: str | None) -> str | None:
        first = previous = None
        for block in stack:
            block_id = self._put(block, previous or parent)
            if previous:
                self.blocks[previous]["next"] = block_id
            else:
                first = block_id
            previous = block_id
        return first

    def add(self, hat: Block, *body: Block) -> None:
        """ハットブロック（旗・メッセージ）から始まるスクリプトを足す。"""
        hat_id = self._put(hat, None, top=True)
        self.blocks[hat_id]["next"] = self._put_stack(list(body), hat_id)

    def define(self, name: str, *body: Block) -> None:
        """引数のないブロック定義を足す。call(name) で呼ぶ。"""
        hat_id = self._put(Block("procedures_definition"), None, top=True)
        proto_id = self._new_id("b", self.blocks)
        self.blocks[proto_id] = {
            "opcode": "procedures_prototype",
            "next": None,
            "parent": hat_id,
            "inputs": {},
            "fields": {},
            "shadow": True,
            "topLevel": False,
            "mutation": _procedure_mutation(name, prototype=True),
        }
        self.blocks[hat_id]["inputs"]["custom_block"] = [1, proto_id]
        self.blocks[hat_id]["next"] = self._put_stack(list(body), hat_id)

    def wrap_in_if(self, block_id: str, cond: Block) -> None:
        """すでにあるブロック1つを「もし cond なら」で包む（前後のつながりは保つ）。"""
        block = self.blocks[block_id]
        parent = self.blocks[block["parent"]]
        guard_id = self._put(when(cond), block["parent"])
        guard = self.blocks[guard_id]
        guard["next"] = block["next"]
        if block["next"]:
            self.blocks[block["next"]]["parent"] = guard_id
        if parent["next"] == block_id:
            parent["next"] = guard_id
        for value in parent["inputs"].values():
            if value[1] == block_id:
                value[1] = guard_id
        guard["inputs"]["SUBSTACK"] = [2, block_id]
        block["parent"], block["next"] = guard_id, None


# ---- プロジェクトを調べる・書き換える ----------------------------------


def find_target(project: dict[str, Any], name: str) -> dict[str, Any]:
    for target in project["targets"]:
        if target["name"] == name:
            return target
    raise ProjectError(f"スプライト「{name}」が元のプロジェクトにありません")


def stage_of(project: dict[str, Any]) -> dict[str, Any]:
    return next(t for t in project["targets"] if t["isStage"])


def blocks_of(target: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """ブロックだけを返す（置いてあるだけの変数は配列で入っているので除く）。"""
    return {k: b for k, b in target["blocks"].items() if isinstance(b, dict)}


def receivers(project: dict[str, Any], message: str) -> list[tuple[dict[str, Any], str]]:
    """メッセージを受け取るスクリプトの（スプライト, ハットブロックのID）。"""
    found = []
    for target in project["targets"]:
        for block_id, block in blocks_of(target).items():
            if (
                block["opcode"] == "event_whenbroadcastreceived"
                and block["fields"]["BROADCAST_OPTION"][0] == message
            ):
                found.append((target, block_id))
    return found


def script_blocks(target: dict[str, Any], top_id: str) -> list[str]:
    """あるスクリプトに属するブロックのIDを、上から順に返す。"""
    blocks = blocks_of(target)
    order, queue = [], [top_id]
    while queue:
        block_id = queue.pop()
        if block_id not in blocks:
            continue
        order.append(block_id)
        block = blocks[block_id]
        children = [v[1] for v in block["inputs"].values() if isinstance(v[1], str)]
        queue.extend([*( [block["next"]] if block["next"] else []), *reversed(children)])
    return order


def rename_stage_variables(project: dict[str, Any], renamed: dict[str, str]) -> None:
    """ステージの変数の名前を変える（変数のID → 新しい名前）。使っているブロックとモニターも直す。"""
    variables = stage_of(project)["variables"]
    for var_id, name in renamed.items():
        variables[var_id][0] = name
    for target in project["targets"]:
        for block in target["blocks"].values():
            if isinstance(block, list):  # 置いてあるだけの変数 [12, 名前, ID, x, y]
                if block[0] == 12 and block[2] in renamed:
                    block[1] = renamed[block[2]]
                continue
            ref = block["fields"].get("VARIABLE")
            if ref and ref[1] in renamed:
                ref[0] = renamed[ref[1]]
            for value in block["inputs"].values():
                for inner in value[1:]:
                    if isinstance(inner, list) and inner[0] == 12 and inner[2] in renamed:
                        inner[1] = renamed[inner[2]]
    for monitor in project.get("monitors", []):
        if monitor.get("id") in renamed:
            monitor["params"]["VARIABLE"] = renamed[monitor["id"]]


# ---- スプライトを足す ------------------------------------------------


@dataclass(frozen=True)
class Costume:
    """SVG で作ったコスチューム。center は絵の中での回転の中心（ふつうは絵のまんなか）。"""

    name: str
    svg: str
    center: tuple[float, float]

    @property
    def data(self) -> bytes:
        return self.svg.encode("utf-8")

    @property
    def md5ext(self) -> str:
        """sb3 の中でのファイル名（中身の MD5 + 拡張子）。"""
        return hashlib.md5(self.data, usedforsecurity=False).hexdigest() + ".svg"


def add_sprite(project: dict[str, Any], name: str, costumes: list[Costume]) -> dict[str, Any]:
    """隠れた状態のスプライトを、いちばん手前の層に足す。スクリプトは Scripts(project, name) で足す。"""
    if any(t["name"] == name for t in project["targets"]):
        raise ProjectError(f"スプライト「{name}」はすでにあります")
    target = {
        "isStage": False,
        "name": name,
        "variables": {},
        "lists": {},
        "broadcasts": {},
        "blocks": {},
        "comments": {},
        "currentCostume": 0,
        "costumes": [
            {
                "name": c.name,
                "bitmapResolution": 1,
                "dataFormat": "svg",
                "assetId": c.md5ext.removesuffix(".svg"),
                "md5ext": c.md5ext,
                "rotationCenterX": c.center[0],
                "rotationCenterY": c.center[1],
            }
            for c in costumes
        ],
        "sounds": [],
        "volume": 100,
        "layerOrder": max(t.get("layerOrder", 0) for t in project["targets"]) + 1,
        "visible": False,
        "x": 0,
        "y": 0,
        "size": 100,
        "direction": 90,
        "draggable": False,
        "rotationStyle": "all around",
    }
    project["targets"].append(target)
    return target
