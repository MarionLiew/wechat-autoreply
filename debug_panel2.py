"""诊断「展开」AXCheckBox 的位置，区分消息气泡 vs 功能浮层按钮"""
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

def _deep_find_all(root, role, max_depth=10):
    out = []
    if max_depth < 0: return out
    try:
        if str(getattr(root, "AXRole", "") or "") == role:
            out.append(root)
    except: pass
    for c in _safe_children(root):
        out.extend(_deep_find_all(c, role, max_depth - 1))
    return out

# 窗口尺寸
win_pos = getattr(main_win, "AXPosition", None)
win_sz  = getattr(main_win, "AXSize", None)
print(f"窗口 pos={win_pos}  sz={win_sz}", flush=True)

# 所有 AXCheckBox（含坐标）
print("\n所有 AXCheckBox:", flush=True)
for cb in _deep_find_all(main_win, "AXCheckBox", max_depth=15):
    title = str(getattr(cb, "AXTitle", "") or "").strip()
    val   = str(getattr(cb, "AXValue", "") or "").strip()
    pos   = getattr(cb, "AXPosition", None)
    sz    = getattr(cb, "AXSize", None)
    print(f"  title={repr(title)}  val={repr(val)}  pos={pos}  sz={sz}", flush=True)

# 所有 AXScrollArea（区分消息区 vs 输入框）
print("\nAXScrollArea:", flush=True)
for s in _deep_find_all(main_win, "AXScrollArea", max_depth=10):
    pos = getattr(s, "AXPosition", None)
    sz  = getattr(s, "AXSize", None)
    print(f"  pos={pos}  sz={sz}", flush=True)

print("done", flush=True)
