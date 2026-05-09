#!/usr/bin/env python3
"""Find all WeCom-related processes and their AX window info"""
import atomacos
from config import settings

print(f"Bundle ID: {settings.wecom_bundle_id}")
print()

all_apps = atomacos.NativeUIElement.getRunningApps()
wecom_apps = []
for a in all_apps:
    bid = a.bundleIdentifier()
    if "WeCom" in (bid or "") or "WeWork" in (bid or "") or "wecom" in (bid or "").lower():
        pid = a.processIdentifier()
        name = a.localizedName()
        print(f"App: name={name} bid={bid} pid={pid}")
        wecom_apps.append((pid, name, bid))

print(f"\nTotal WeCom-related apps: {len(wecom_apps)}")

# Also check by bundle ID directly
try:
    app = atomacos.getAppRefByBundleId(settings.wecom_bundle_id)
    print(f"\ngetAppRefByBundleId result: {app}")
    try:
        wins = app.AXWindows
        print(f"  AXWindows: {len(wins)}")
        for w in wins:
            sz = getattr(w, "AXSize", None)
            pos = getattr(w, "AXPosition", None)
            title = getattr(w, "AXTitle", None)
            print(f"    Window: title={title} pos={pos} sz={sz}")
            try:
                kids = w.AXChildren
                print(f"    Children: {len(kids)}")
                for k in kids[:5]:
                    role = getattr(k, "AXRole", "")
                    ksz = getattr(k, "AXSize", None)
                    kpos = getattr(k, "AXPosition", None)
                    print(f"      {role} pos={kpos} sz={ksz}")
            except Exception as e:
                print(f"    Children error: {e}")
    except Exception as e:
        print(f"  AXWindows error: {e}")
except Exception as e:
    print(f"getAppRefByBundleId error: {e}")

# Try each pid
for pid, name, bid in wecom_apps:
    print(f"\n--- PID {pid} ({name}) ---")
    try:
        app = atomacos.getAppRefByPid(pid)
        try:
            wins = app.AXWindows
            print(f"  AXWindows: {len(wins)}")
            for w in wins:
                sz = getattr(w, "AXSize", None)
                pos = getattr(w, "AXPosition", None)
                title = getattr(w, "AXTitle", None)
                print(f"    Window: title={title} pos={pos} sz={sz}")
                try:
                    kids = w.AXChildren
                    print(f"    Children count: {len(kids)}")
                    for k in kids[:8]:
                        role = getattr(k, "AXRole", "")
                        ksz = getattr(k, "AXSize", None)
                        kpos = getattr(k, "AXPosition", None)
                        print(f"      {role} pos={kpos} sz={ksz}")
                except Exception as e:
                    print(f"    Children error: {e}")
        except Exception as e:
            print(f"  AXWindows error: {e}")
    except Exception as e:
        print(f"  getAppRefByPid error: {e}")

print("\ndone")
