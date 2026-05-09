"""dump 左侧导航区域的所有可交互元素，找经营线索模块入口"""
import sys
sys.path.insert(0, ".")
import atomacos
from config import settings

app = atomacos.getAppRefByBundleId(settings.wecom_bundle_id)
windows = app.AXWindows
main_win = next(
    (w for w in windows if str(getattr(w, "AXTitle", "") or "") == "企业微信"),
    windows[0],
)

def _safe_children(el):
    try: return el.AXChildren or []
    except: return []

def dump_node(node, depth=0, max_depth=5):
    if depth > max_depth: return
    role  = str(getattr(node, "AXRole", "") or "")
    title = str(getattr(node, "AXTitle", "") or "")[:50]
    val   = str(getattr(node, "AXValue", "") or "")[:50]
    desc  = str(getattr(node, "AXDescription", "") or "")[:50]
    pos   = getattr(node, "AXPosition", None)
    sz    = getattr(node, "AXSize", None)

    # 只关注 x < 500 的元素（左侧导航区）
    if pos and pos[0] > 500:
        return
    text = title or val or desc
    if text or role in ("AXButton", "AXLink", "AXImage", "AXToolbar", "AXGroup"):
        indent = "  " * depth
        line = f"{indent}[{role}]"
        if title: line += f" title={repr(title)}"
        if val:   line += f" val={repr(val)}"
        if desc:  line += f" desc={repr(desc)}"
        line += f" pos={pos} sz={sz}"
        print(line, flush=True)
    for ch in _safe_children(node):
        dump_node(ch, depth+1, max_depth)

print("=== 左侧导航区（x<500）===", flush=True)
dump_node(main_win, max_depth=6)
print("done", flush=True)
