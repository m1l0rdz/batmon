import json
import urllib.request
import urllib.error
import webbrowser

import rumps

API_URL = "http://127.0.0.1:8899/api/now"
BATTERY_SETTINGS_URL = "http://127.0.0.1:8899/api/open_battery_settings"
DASHBOARD_URL = "http://127.0.0.1:8899/"
AWAKE_URL = "http://127.0.0.1:8899/api/awake"
CMD_URL = "http://127.0.0.1:8899/api/cmd"

class BatmonApp(rumps.App):
    def __init__(self):
        super(BatmonApp, self).__init__("batmon: init...")
        self.title = "batmon: init..."
        self.menu = []
        self.last_anomaly_id = 0
        # Until the first check seeds this to the latest existing anomaly id,
        # do not fire notifications - otherwise every historical anomaly
        # re-notifies on each (re)start (launchd KeepAlive respawns us).
        self._anomalies_seeded = False
        self.update_menu(None)

    def _fmt_minutes(self, m):
        return f"{m // 60}h {m % 60}m"

    @rumps.timer(10)
    def update_menu(self, _):
        try:
            req = urllib.request.Request(API_URL)
            with urllib.request.urlopen(req, timeout=2.0) as response:
                data = json.loads(response.read().decode("utf-8"))

            sample = data.get("sample") or {}
            soc = sample.get("soc_pct") or 0
            watts = sample.get("watts", 0.0)

            awake_str = "☕ " if data.get("awake") else ""
            icon_str = "^" if watts >= 0 else "v"
            
            forecast = data.get("forecast") or {}
            f_mins = forecast.get("minutes")
            time_str = f" {f_mins // 60}:{f_mins % 60:02d}" if f_mins is not None else ""

            self.title = f"{awake_str}{abs(watts):.1f}W {icon_str} {int(soc)}%{time_str}"

            self.rebuild_menu(data)

        except Exception:
            self.title = "batmon: API error"
            self.rebuild_menu({})

        self.check_anomalies()

    def check_anomalies(self):
        try:
            req = urllib.request.Request(f"http://127.0.0.1:8899/api/anomalies?since={self.last_anomaly_id}")
            with urllib.request.urlopen(req, timeout=2.0) as response:
                anomalies = json.loads(response.read().decode("utf-8"))

            if not self._anomalies_seeded:
                # First successful poll: adopt the latest id without notifying,
                # so only anomalies raised from now on trigger notifications.
                if anomalies:
                    self.last_anomaly_id = max(a["id"] for a in anomalies)
                self._anomalies_seeded = True
                return

            for a in anomalies:
                app_name = a["app"]
                if app_name == "__SYSTEM_SLEEP_DRAIN__":
                    title = "😴 Excessive Sleep Drain"
                    body = f"Battery dropped {a['wh_today']:.1f}% while asleep"
                elif app_name == "__SYSTEM_RAPID_DISCHARGE__":
                    title = "⚡ Rapid Discharge"
                    body = f"Average discharge is {a['wh_today']:.1f}W over last 15 mins"
                elif app_name == "__SYSTEM_WEAK_CHARGER__":
                    title = "🔌 Weak Charger"
                    body = f"Battery is draining {a['wh_today']:.1f}W even though it is plugged in"
                elif app_name == "__SYSTEM_THERMAL__":
                    title = "🌡️ High Thermal Pressure"
                    body = f"System thermal pressure is elevated ({int(a['wh_today'])} mins today)"
                else:
                    title = f"batmon: {app_name} anomaly"
                    body = f"{a['wh_today']:.1f} Wh today vs {a['wh_baseline']:.1f} Wh baseline ({a['ratio']:.1f}x)"

                detail = a.get("detail")
                if detail:
                    culprits = detail.get("culprits", [])
                    advice = detail.get("advice", "")
                    culprits_str = ", ".join([c["app"] for c in culprits])
                    if culprits_str:
                        body += f" - Top: {culprits_str}"
                    if advice:
                        body += f" - Try: {advice}"

                rumps.notification(title, "", body)
                self.last_anomaly_id = a["id"]
        except Exception:
            pass

    def rebuild_menu(self, data):
        self.menu.clear()

        f = data.get("forecast") or {}
        if f.get("minutes") is not None:
            verb = "full in" if f.get("mode") == "charging" else "left"
            if verb == "left":
                self.menu.add(f"{self._fmt_minutes(f['minutes'])} left")
            else:
                self.menu.add(f"full in {self._fmt_minutes(f['minutes'])}")

        h = data.get("health")
        if h:
            temp = (data.get("sample") or {}).get("temp_c")
            temp_str = f"{temp:.1f} C" if temp is not None else "-"
            self.menu.add(
                f"{(h.get('raw_current_capacity_mah') or 0):.0f} mAh - {temp_str} - health {(h.get('max_capacity_pct') or 0):.0f}% - {h.get('cycle_count') or 0} cycles"
            )

        c = data.get("component")
        if c and c.get("package_mw") is not None:
            self.menu.add(f"CPU {c.get('cpu_mw') or 0:.0f} - GPU {c.get('gpu_mw') or 0:.0f} - pkg {c.get('package_mw'):.0f} mW")

        sc = data.get("score") or {}
        if sc.get("score") is not None:
            self.menu.add(f"Score {sc['score']}/100 ({sc.get('grade', '')})")

        sess = data.get("session")
        if sess and sess.get("soc_now") is not None:
            soc_start = sess.get("soc_start")
            delta = sess["soc_now"] - (soc_start if soc_start is not None else sess["soc_now"])
            kind_str = "On battery" if sess.get("kind") == "battery" else "On AC"
            sign = "+" if delta >= 0 else "-"
            dur_mins = (sess.get("duration_sec") or 0) // 60
            self.menu.add(f"{kind_str} {self._fmt_minutes(dur_mins)} - {sign}{abs(delta):.0f}%")

        self.menu.add(rumps.separator)

        self.menu.add("Top apps, last hour")
        for a in (data.get("top_apps") or [])[:5]:
            awh = a.get("attributed_wh") or 0
            if awh < 0.01:
                energy = f"{awh * 1000:.1f} mWh"
            else:
                energy = f"{awh:.2f} Wh"
            self.menu.add(f"{a.get('app', 'Unknown')} - {energy}")

        self.menu.add(rumps.separator)

        awake_state = bool(data.get("awake"))
        awake_item = rumps.MenuItem("Keep awake", callback=self.toggle_awake)
        awake_item.state = awake_state
        self.menu.add(awake_item)

        cl = data.get("charge_limit") or {}
        level = cl.get("level", 80)
        holding = cl.get("holding")
        state_str = {True: "active", False: "off"}.get(holding, "?")
        peak = cl.get("todays_peak_soc")
        peak_txt = f" (peak {peak:.0f}%)" if peak is not None else ""
        self.menu.add(f"Battery limit {level}%: {state_str}{peak_txt}")

        self.menu.add(rumps.MenuItem("Open Battery Settings", callback=self.open_battery_settings))

        lpm_val = data.get("lpm", "0")
        lpm_state = str(lpm_val) == "1"
        lpm_item = rumps.MenuItem("Low Power Mode", callback=self.toggle_lpm)
        lpm_item.state = lpm_state
        self.menu.add(lpm_item)

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Open dashboard", callback=self.open_dashboard))

    def toggle_awake(self, sender):
        new_state = not sender.state
        req = urllib.request.Request(AWAKE_URL, method="POST", data=json.dumps({"on": new_state}).encode("utf-8"), headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=2.0)
        except Exception:
            pass
        self.update_menu(None)

    def toggle_lpm(self, sender):
        new_state = not sender.state
        req = urllib.request.Request(CMD_URL, method="POST", data=json.dumps({"cmd": "lpm", "args": {"enabled": new_state}}).encode("utf-8"), headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=2.0)
        except Exception:
            pass
        self.update_menu(None)

    def open_dashboard(self, _):
        webbrowser.open(DASHBOARD_URL)

    def open_battery_settings(self, _):
        try:
            req = urllib.request.Request(
                BATTERY_SETTINGS_URL, method="POST",
                headers={"X-Batmon-Client": "1"})
            urllib.request.urlopen(req, timeout=2.0)
        except Exception:
            pass

if __name__ == "__main__":
    BatmonApp().run()
