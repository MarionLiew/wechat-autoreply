"""快速诊断：当前 AXWebArea / AXCheckBox 状态"""
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
print(f"窗口: {getattr(main_win, 'AXTitle', '')}", flush=True)

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

webareas = _deep_find_all(main_win, "AXWebArea", max_depth=15)
print(f"AXWebArea: {len(webareas)} 个", flush=True)
for w in webareas:
    desc = str(getattr(w, "AXDescription", "") or "").strip()
    pos  = getattr(w, "AXPosition", None)
    sz   = getattr(w, "AXSize", None)
    print(f"  desc={repr(desc)}  pos={pos}  sz={sz}", flush=True)

cbs = _deep_find_all(main_win, "AXCheckBox", max_depth=15)
print(f"\nAXCheckBox: {len(cbs)} 个", flush=True)
for cb in cbs:
    title = str(getattr(cb, "AXTitle", "") or "").strip()
    val   = str(getattr(cb, "AXValue", "") or "").strip()
    if title:
        print(f"  title={repr(title)}  value={repr(val)}", flush=True)

print("done", flush=True)
