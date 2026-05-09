"""dump 经营线索面板第一个线索行的子树"""
import sys, time
sys.path.insert(0, ".")
import atomacos
import Quartz
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

def _deep_find_all(root, role, max_depth=8):
    out = []
    if max_depth < 0: return out
    try:
        if str(getattr(root, "AXRole", "") or "") == role: out.append(root)
    except: pass
    for c in _safe_children(root):
        out.extend(_deep_find_all(c, role, max_depth-1))
    return out

tables = _deep_find_all(main_win, "AXTable", max_depth=6)
lead_table = max(tables, key=lambda t: (getattr(t, "AXSize", None) or [0,0])[0])
rows = [c for c in _safe_children(lead_table)
        if str(getattr(c, "AXRole","") or "") == "AXRow"]
print(f"线索行数: {len(rows)}", flush=True)

def dump_subtree(node, depth=0, max_depth=6):
    if depth > max_depth: return
    role  = str(getattr(node, "AXRole",  "") or "")
    title = str(getattr(node, "AXTitle", "") or "")[:60]
    val   = str(getattr(node, "AXValue", "") or "")[:60]
    desc  = str(getattr(node, "AXDescription","") or "")[:60]
    pos   = getattr(node, "AXPosition", None)
    sz    = getattr(node, "AXSize", None)
    indent = "  " * depth
    line = f"{indent}[{role}]"
    if title: line += f" title={repr(title)}"
    if val:   line += f" val={repr(val)}"
    if desc:  line += f" desc={repr(desc)}"
    line += f" pos={pos} sz={sz}"
    print(line, flush=True)
    for ch in _safe_children(node):
        dump_subtree(ch, depth+1, max_depth)

# hover 第三行（最大的那个，height=160，可能是展开的线索卡片）
if len(rows) >= 3:
    row2 = rows[2]
    pos = getattr(row2, "AXPosition", None)
    sz  = getattr(row2, "AXSize", None)
    cx  = pos[0] + sz[0]/2
    cy  = pos[1] + sz[1]/2
    print(f"\nhover 第2行 中心=({cx:.0f},{cy:.0f})", flush=True)
    pt = Quartz.CGPointMake(cx, cy)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap,
        Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, pt, Quartz.kCGMouseButtonLeft))
    time.sleep(0.5)

    print("\n=== 第2行子树 ===", flush=True)
    dump_subtree(row2, max_depth=6)

    # 也 hover 第1行
    row1 = rows[1]
    pos1 = getattr(row1, "AXPosition", None)
    sz1  = getattr(row1, "AXSize", None)
    if pos1 and sz1:
        cx1 = pos1[0] + sz1[0]/2
        cy1 = pos1[1] + sz1[1]/2
        pt1 = Quartz.CGPointMake(cx1, cy1)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap,
            Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, pt1, Quartz.kCGMouseButtonLeft))
        time.sleep(0.5)
        print("\n=== 第1行子树（hover后）===", flush=True)
        dump_subtree(row1, max_depth=6)

print("done", flush=True)
