#!/usr/bin/env python3
"""
从 XMind 8 格式 .xmind 文件解包读取测试用例节点，输出 JSON 供 opentest-case-review 评审使用。

用法：
    python read_cases_xmind.py --xmind path/to/cases.xmind
"""
import argparse
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple


NS = "urn:xmind:xmap:xmlns:content:2.0"
NS_TAG = f"{{{NS}}}"

# OpenTest 生成器写入的字段子节点标题前缀
FIELD_PREFIXES = {
    "preconditions": ("前置条件", "前置条件:"),
    "steps": ("测试步骤", "测试步骤:"),
    "expected": ("预期结果", "预期结果:"),
}

# 分类节点标题模式（非用例叶子）
_CATEGORY_PATTERNS = re.compile(r"(用例|测试)$|正向|负向|边界|异常")


def _local_tag(elem: ET.Element) -> str:
    if elem.tag.startswith("{"):
        return elem.tag.split("}", 1)[1]
    return elem.tag


def _find_child(parent: ET.Element, local_name: str) -> Optional[ET.Element]:
    for child in parent:
        if _local_tag(child) == local_name:
            return child
    return None


def _topic_title(topic: ET.Element) -> str:
    title_el = _find_child(topic, "title")
    if title_el is not None and title_el.text:
        return title_el.text.strip()
    return ""


def _topic_notes(topic: ET.Element) -> str:
    notes = _find_child(topic, "notes")
    if notes is None:
        return ""
    plain = _find_child(notes, "plain")
    if plain is not None and plain.text:
        return plain.text.strip()
    return ""


def _topic_children(topic: ET.Element) -> List[ET.Element]:
    children_wrap = _find_child(topic, "children")
    if children_wrap is None:
        return []
    result: List[ET.Element] = []
    for elem in children_wrap.iter():
        if _local_tag(elem) == "topic" and elem is not topic:
            # 只取直接子 topic（避免重复遍历嵌套）
            pass
    # 直接 attached topics
    for child in children_wrap:
        if _local_tag(child) == "topics":
            for t in child:
                if _local_tag(t) == "topic":
                    result.append(t)
    return result


def _parse_field_from_title(title: str) -> Tuple[Optional[str], str]:
    """从子节点标题解析字段类型与内容，如「前置条件: xxx」。"""
    for field, prefixes in FIELD_PREFIXES.items():
        for prefix in prefixes:
            if title.startswith(prefix):
                rest = title[len(prefix) :].strip()
                if rest.startswith(":"):
                    rest = rest[1:].strip()
                return field, rest
    return None, title


def _extract_fields_from_subtree(topic: ET.Element) -> Dict[str, str]:
    """DFS 提取子树中所有前置/步骤/预期（支持 OpenTest 嵌套字段链）。"""
    fields: Dict[str, str] = {"preconditions": "", "steps": "", "expected": ""}

    def walk(node: ET.Element) -> None:
        title = _topic_title(node)
        field, value = _parse_field_from_title(title)
        if field and value and not fields[field]:
            fields[field] = value
        for child in _topic_children(node):
            walk(child)

    for child in _topic_children(topic):
        walk(child)
    return fields


def _is_category_node(title: str, has_case_like_child: bool) -> bool:
    if not title:
        return True
    if _CATEGORY_PATTERNS.search(title):
        return True
    if title.endswith(" - 测试用例"):
        return True
    if has_case_like_child and title.endswith("用例") and len(title) < 12:
        return True
    return False


def _is_leaf_case(topic: ET.Element, title: str, children: List[ET.Element]) -> bool:
    if not title:
        return False
    if _is_category_node(title, bool(children)):
        return False
    # 有字段子节点或没有子节点 -> 视为用例
    for child in children:
        ct = _topic_title(child)
        field, _ = _parse_field_from_title(ct)
        if field:
            return True
    if not children:
        return True
    # 子节点含字段链（含嵌套）
    if children:
        fields = _extract_fields_from_subtree(topic)
        if any(fields.values()):
            return True
    return False


def _walk_topics(
    topic: ET.Element,
    path: List[str],
    cases: List[Dict[str, Any]],
    source_file: str,
    index: List[int],
) -> None:
    title = _topic_title(topic)
    notes = _topic_notes(topic)
    children = _topic_children(topic)

    if _is_leaf_case(topic, title, children):
        fields = _extract_fields_from_subtree(topic)
        index[0] += 1
        case: Dict[str, Any] = {
            "index": index[0],
            "name": title,
            "path": " / ".join(path + [title]) if path else title,
            "feature": _infer_feature_from_path(path),
            "type": _infer_type_from_path(path),
            "preconditions": fields.get("preconditions", ""),
            "steps": fields.get("steps", ""),
            "expected": fields.get("expected", ""),
            "notes": notes,
            "priority": _infer_priority_from_markers(topic),
            "raw_title": title,
            "source": "xmind",
            "source_file": source_file,
        }
        cases.append(case)
        return

    new_path = path + [title] if title else path
    for child in children:
        _walk_topics(child, new_path, cases, source_file, index)


def _infer_feature_from_path(path: List[str]) -> str:
    for segment in path:
        if segment.endswith(" - 测试用例"):
            return segment.replace(" - 测试用例", "").strip()
        if segment and not _CATEGORY_PATTERNS.search(segment) and "测试用例" not in segment:
            if segment not in ("测试用例",):
                return segment
    return path[0] if path else ""


def _infer_type_from_path(path: List[str]) -> str:
    for segment in reversed(path):
        for t in ("正向", "负向", "边界", "异常"):
            if t in segment:
                return t
    return ""


def _infer_priority_from_markers(topic: ET.Element) -> str:
    marker_refs = _find_child(topic, "marker-refs")
    if marker_refs is None:
        return ""
    for ref in marker_refs:
        if _local_tag(ref) != "marker-ref":
            continue
        mid = ref.get("marker-id", "")
        if mid == "priority-1":
            return "P0"
        if mid == "priority-2":
            return "P1"
        if mid == "priority-3":
            return "P2"
    return ""


def _load_content_xml(xmind_path: str) -> bytes:
    with zipfile.ZipFile(xmind_path, "r") as zf:
        names = zf.namelist()
        for candidate in ("content.xml", "content.json"):
            if candidate in names:
                return zf.read(candidate)
        # XMind Zen / 2020 可能用 content.json
        raise FileNotFoundError(f"未在 {xmind_path} 中找到 content.xml")


def read_cases_from_xmind(xmind_path: str) -> List[Dict[str, Any]]:
    raw = _load_content_xml(xmind_path)
    root = ET.fromstring(raw)
    cases: List[Dict[str, Any]] = []
    index = [0]

    for sheet in root.iter():
        if _local_tag(sheet) != "sheet":
            continue
        root_topic = _find_child(sheet, "topic")
        if root_topic is None:
            continue
        sheet_title = _topic_title(root_topic) or "测试用例"
        children = _topic_children(root_topic)
        if not children:
            _walk_topics(root_topic, [], cases, xmind_path, index)
        else:
            for child in children:
                _walk_topics(child, [sheet_title], cases, xmind_path, index)

    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Read test cases from XMind for case review")
    parser.add_argument("--xmind", "-m", required=True, help="Path to .xmind file")
    args = parser.parse_args()

    try:
        cases = read_cases_from_xmind(args.xmind)
        out = {
            "source": args.xmind,
            "count": len(cases),
            "cases": cases,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e), "path": args.xmind}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
