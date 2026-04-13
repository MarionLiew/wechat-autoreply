"""
诊断脚本：dump 企业微信 Mac 客户端的 Accessibility 树。

用法：
    python3 dump_ax_tree.py > ax_tree.txt

前置条件：
    1. 企业微信已启动，且主窗口可见（不是只剩 Dock 图标）
    2. 系统设置 → 隐私 → 辅助功能 已勾选运行 Python 的终端

产出：
    - 打印整棵 AX 树（限制深度），包含每个节点的 Role/Title/Value/Identifier/Description
    - 把结果发给开发者即可定位正确的选择器
"""

import sys
import atomacos
from config import settings


MAX_DEPTH = 8
MAX_CHILDREN_PER_NODE = 50


def safe_attr(el, name: str) -> str:
    try:
        v = getattr(el, name, None)
        if v is None:
            return ""
        s = str(v)
        return s[:80].replace("\n", " ")
    except Exception:
        return ""


def dump(el, depth: int = 0, prefix: str = "") -> None:
    role = safe_attr(el, "AXRole")
    ident = safe_attr(el, "AXIdentifier")
    title = safe_attr(el, "AXTitle")
    value = safe_attr(el, "AXValue")
    desc = safe_attr(el, "AXDescription")

    line = f"{prefix}[{role}]"
    if ident:
        line += f" id={ident!r}"
    if title:
        line += f" title={title!r}"
    if value:
        line += f" value={value!r}"
    if desc:
        line += f" desc={desc!r}"
    print(line)

    if depth >= MAX_DEPTH:
        print(f"{prefix}  … (达到最大深度，省略)")
        return

    try:
        children = el.AXChildren or []
    except Exception as exc:
        print(f"{prefix}  <无法获取子节点: {exc}>")
        return

    for i, child in enumerate(children[:MAX_CHILDREN_PER_NODE]):
        dump(child, depth + 1, prefix + "  ")
    if len(children) > MAX_CHILDREN_PER_NODE:
        print(f"{prefix}  … 还有 {len(children) - MAX_CHILDREN_PER_NODE} 个子节点未展示")


def main() -> None:
    bundle_id = settings.wecom_bundle_id
    print(f"=== 尝试连接企业微信（bundle_id={bundle_id}）===")
    try:
        app = atomacos.getAppRefByBundleId(bundle_id)
    except Exception as exc:
        print(f"失败：{exc}")
        print("请确认：1) 企业微信已启动  2) bundle_id 正确  3) 辅助功能权限已勾选")
        sys.exit(1)

    try:
        windows = app.AXWindows
    except Exception as exc:
        print(f"无法枚举窗口：{exc}")
        print("→ 通常是缺少辅助功能权限；请到系统设置勾选并重启终端")
        sys.exit(1)

    print(f"窗口数量：{len(windows)}")
    for idx, win in enumerate(windows):
        print(f"\n===== 窗口 {idx} =====")
        dump(win)


if __name__ == "__main__":
    main()
