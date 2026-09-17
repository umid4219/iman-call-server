from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timedelta
from html import escape
import os
import json
import re
import pandas as pd

app = FastAPI()

RECORDINGS_DIR = "recordings"
DATA_FILE = "data/calls_data.json"
SYNC_FILE = "data/last_sync.json"
os.makedirs(RECORDINGS_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

app.mount("/recordings", StaticFiles(directory=RECORDINGS_DIR), name="recordings")


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_sync_status():
    if os.path.exists(SYNC_FILE):
        with open(SYNC_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


def save_sync_status(data):
    with open(SYNC_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def format_duration(seconds_str):
    try:
        sec = int(float(seconds_str))
    except (ValueError, TypeError):
        sec = 0
    if sec == 0:
        return "0 сек"
    minutes = sec // 60
    remaining_sec = sec % 60
    if minutes == 0:
        return f"{remaining_sec} сек"
    return f"{minutes} мин {remaining_sec} сек"


def duration_to_seconds(value):
    """Supports both the formatted value and a numeric duration if it exists."""
    if value is None:
        return 0
    text = str(value).lower().strip()
    if text.isdigit():
        return int(text)
    minutes = re.search(r"(\d+)\s*мин", text)
    seconds = re.search(r"(\d+)\s*сек", text)
    return (int(minutes.group(1)) * 60 if minutes else 0) + (
        int(seconds.group(1)) if seconds else 0
    )


def format_total_duration(seconds):
    if seconds < 60:
        return f"{seconds} сек"
    return f"{seconds // 60} мин {seconds % 60} сек"


def type_class(call_type):
    return {
        "входящий": "incoming",
        "исходящий": "outgoing",
        "пропущенный": "missed",
    }.get(str(call_type).lower(), "other")


def connection_label(value):
    return value if value else "Нет данных"


def employee_statistics(calls, employee):
    employee_calls = [c for c in calls if c.get("employee") == employee]
    incoming = sum(1 for c in employee_calls if str(c.get("type", "")).lower() == "входящий")
    outgoing = sum(1 for c in employee_calls if str(c.get("type", "")).lower() == "исходящий")
    missed = sum(1 for c in employee_calls if str(c.get("type", "")).lower() == "пропущенный")
    total_seconds = sum(duration_to_seconds(c.get("duration_formatted")) for c in employee_calls)
    return {
        "total": len(employee_calls),
        "incoming": incoming,
        "outgoing": outgoing,
        "missed": missed,
        "duration": format_total_duration(total_seconds),
    }


def render_call_item(call):
    call_type = escape(str(call.get("type", "Неизвестно")))
    audio_url = call.get("audio_url", "")
    audio_html = (
        f'<audio controls preload="none" src="{escape(audio_url, quote=True)}"></audio>'
        f'<a class="download-link" href="{escape(audio_url, quote=True)}" download>Скачать</a>'
        if audio_url
        else '<span class="no-recording">Нет записи</span>'
    )
    return f"""
        <div class="call-row">
            <div class="call-date">{escape(str(call.get("datetime", "—")))}</div>
            <div class="call-number">{escape(str(call.get("number", "—")))}</div>
            <div><span class="badge {type_class(call.get("type"))}">{call_type}</span></div>
            <div class="call-duration">{escape(str(call.get("duration_formatted", "0 сек")))}</div>
            <div class="call-recording">{audio_html}</div>
        </div>
    """


def render_employee_card(employee, calls, sync_status):
    stats = employee_statistics(calls, employee)
    employee_html = escape(str(employee))
    employee_calls = sorted(calls, key=lambda c: c.get("timestamp", 0), reverse=True)
    calls_html = "".join(render_call_item(call) for call in employee_calls)
    if not calls_html:
        calls_html = '<div class="empty-state compact">Нет звонков по выбранным фильтрам</div>'

    return f"""
        <details class="employee-card" data-employee="{employee_html}">
            <summary>
                <span class="employee-main">
                    <span class="employee-avatar">{employee_html[:1].upper()}</span>
                    <span>
                        <strong>{employee_html}</strong>
                        <small>Последняя связь: {escape(connection_label(sync_status.get(employee)))}</small>
                    </span>
                </span>
                <span class="employee-metrics">
                    <span class="metric total"><b>{stats["total"]}</b><em>всего</em></span>
                    <span class="metric incoming"><b>{stats["incoming"]}</b><em>входящих</em></span>
                    <span class="metric outgoing"><b>{stats["outgoing"]}</b><em>исходящих</em></span>
                    <span class="metric missed"><b>{stats["missed"]}</b><em>пропущенных</em></span>
                    <span class="metric duration"><b>{escape(stats["duration"])}</b><em>разговоры</em></span>
                </span>
                <span class="chevron" aria-hidden="true">⌄</span>
            </summary>
            <div class="employee-details">
                <div class="calls-heading">
                    <span>Звонки сотрудника</span>
                    <span>{stats["total"]} записей</span>
                </div>
                <div class="calls-list">
                    <div class="call-row call-head">
                        <div>Дата / время</div><div>Номер</div><div>Тип</div>
                        <div>Длительность</div><div>Запись</div>
                    </div>
                    {calls_html}
                </div>
            </div>
        </details>
    """


@app.post("/api/upload-call")
async def upload_call(
    employee: str = Form("Сотрудник"),
    number: str = Form(None),
    duration: str = Form("0"),
    call_type: str = Form("Пропущенный"),
    date_timestamp: int = Form(None),
    file: UploadFile = File(None),
):
    # Android integration contract is intentionally unchanged.
    try:
        current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        sync_status = load_sync_status()
        sync_status[employee] = current_time_str
        save_sync_status(sync_status)

        if not number or not date_timestamp:
            return {"status": "success", "message": "Ping received, no new calls"}

        file_path = ""
        if file and file.filename:
            file_location = os.path.join(RECORDINGS_DIR, file.filename)
            with open(file_location, "wb+") as f:
                f.write(await file.read())
            file_path = f"/recordings/{file.filename}"

        if call_type.lower() == "пропущенный":
            duration = "0"

        calls = load_data()
        new_call = {
            "employee": employee,
            "number": number,
            "duration_formatted": format_duration(duration),
            "type": call_type,
            "timestamp": date_timestamp,
            "datetime": datetime.fromtimestamp(date_timestamp / 1000).strftime("%Y-%m-%d %H:%M:%S"),
            "audio_url": file_path,
        }

        exists = any(
            c["employee"] == employee
            and c["timestamp"] == date_timestamp
            and c["number"] == number
            for c in calls
        )
        if not exists:
            calls.insert(0, new_call)

        cutoff_time = (datetime.now() - timedelta(hours=48)).timestamp() * 1000
        calls = [c for c in calls if c["timestamp"] >= cutoff_time]

        save_data(calls)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/", response_class=HTMLResponse)
async def get_dashboard(employee: str = "all", date: str = None, call_type: str = "all"):
    calls = load_data()
    sync_status = load_sync_status()
    employees = sorted(list(set(c.get("employee", "Сотрудник") for c in calls).union(sync_status.keys())))

    filtered_calls = calls
    if employee != "all":
        filtered_calls = [c for c in filtered_calls if c.get("employee") == employee]
    if date:
        filtered_calls = [c for c in filtered_calls if str(c.get("datetime", "")).startswith(date)]
    if call_type != "all":
        filtered_calls = [
            c for c in filtered_calls if str(c.get("type", "")).lower() == call_type.lower()
        ]
    filtered_calls = sorted(filtered_calls, key=lambda c: c.get("timestamp", 0), reverse=True)

    selected_calls_by_employee = {
        emp: [c for c in filtered_calls if c.get("employee") == emp] for emp in employees
    }
    visible_employees = employees if employee == "all" else [employee]
    cards_html = "".join(
        render_employee_card(emp, selected_calls_by_employee.get(emp, []), sync_status)
        for emp in visible_employees
        if emp in employees
    )
    if not cards_html:
        cards_html = '<div class="empty-state">Нет сотрудников по выбранным фильтрам</div>'

    employee_options = '<option value="all">Все сотрудники</option>'
    for emp in employees:
        selected = "selected" if emp == employee else ""
        employee_options += f'<option value="{escape(emp, quote=True)}" {selected}>{escape(emp)}</option>'

    type_options = f"""
        <option value="all" {"selected" if call_type == "all" else ""}>Все типы звонков</option>
        <option value="Входящий" {"selected" if call_type == "Входящий" else ""}>Входящие</option>
        <option value="Исходящий" {"selected" if call_type == "Исходящий" else ""}>Исходящие</option>
        <option value="Пропущенный" {"selected" if call_type == "Пропущенный" else ""}>Пропущенные</option>
    """
    total_stats = employee_statistics(filtered_calls, "__all__")
    total_stats["total"] = len(filtered_calls)
    total_stats["incoming"] = sum(
        1 for c in filtered_calls if str(c.get("type", "")).lower() == "входящий"
    )
    total_stats["outgoing"] = sum(
        1 for c in filtered_calls if str(c.get("type", "")).lower() == "исходящий"
    )
    total_stats["missed"] = sum(
        1 for c in filtered_calls if str(c.get("type", "")).lower() == "пропущенный"
    )
    total_seconds = sum(duration_to_seconds(c.get("duration_formatted")) for c in filtered_calls)
    total_stats["duration"] = format_total_duration(total_seconds)

    filter_query = "&".join(
        part
        for part in [
            f"employee={escape(employee, quote=True)}" if employee != "all" else "",
            f"date={escape(date, quote=True)}" if date else "",
            f"call_type={escape(call_type, quote=True)}" if call_type != "all" else "",
        ]
        if part
    )
    refresh_url = f"/?{filter_query}" if filter_query else "/"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
        <title>IMAN — Журнал звонков</title>
        <style>
            :root {{
                --bg: #100d0b; --panel: #191410; --panel-light: #211b16;
                --border: #392d21; --gold: #d4af37; --gold-light: #f0ce5b;
                --muted: #9d8b72; --text: #eee5d5; --green: #44d483;
                --blue: #4da3ff; --red: #ef6c63;
            }}
            * {{ box-sizing: border-box; }}
            body {{
                background: radial-gradient(circle at 10% 0%, #261a11 0, transparent 35%),
                            linear-gradient(135deg, #100d0b, #17110d 60%, #0c0a08);
                background-attachment: fixed; color: var(--text);
                font-family: Inter, "Segoe UI", sans-serif; margin: 0; min-height: 100vh;
            }}
            .container {{ width: min(1380px, calc(100% - 48px)); margin: 0 auto; padding: 34px 0 52px; }}
            .header {{ display: flex; justify-content: space-between; gap: 25px; align-items: flex-start; margin-bottom: 25px; }}
            h1 {{ color: var(--gold); font-size: clamp(22px, 3vw, 34px); letter-spacing: 1px; margin: 0; text-transform: uppercase; }}
            .sub-title {{ color: #c39b2c; font-size: 14px; margin-top: 7px; }}
            .signature {{ color: #80652e; font-size: 11px; margin-top: 6px; font-style: italic; }}
            .live-status {{ color: var(--muted); display: flex; gap: 9px; align-items: center; white-space: nowrap; font-size: 12px; padding-top: 7px; }}
            .live-dot {{ width: 9px; height: 9px; border-radius: 50%; background: var(--green); box-shadow: 0 0 12px var(--green); }}
            .card {{ background: rgba(25, 20, 16, .92); border: 1px solid var(--border); border-radius: 14px; box-shadow: 0 14px 45px rgba(0,0,0,.18); }}
            .filters {{ display: flex; gap: 12px; align-items: end; flex-wrap: wrap; padding: 18px; }}
            .filter-group {{ display: flex; flex-direction: column; gap: 7px; min-width: 185px; flex: 1; }}
            .filter-group label {{ color: var(--gold); font-size: 10px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }}
            select, input[type="date"] {{ width: 100%; appearance: auto; background: #120f0c; color: var(--text); border: 1px solid #55402a; padding: 11px 12px; border-radius: 8px; font-size: 13px; outline: none; }}
            select:focus, input[type="date"]:focus {{ border-color: var(--gold); box-shadow: 0 0 0 3px rgba(212,175,55,.1); }}
            .filter-actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
            .btn {{ background: var(--gold); color: #17110a; border: 0; padding: 11px 16px; border-radius: 8px; cursor: pointer; font-weight: 800; text-decoration: none; display: inline-flex; align-items: center; justify-content: center; font-size: 13px; white-space: nowrap; }}
            .btn:hover {{ background: var(--gold-light); }}
            .btn-secondary {{ background: #17120e; color: var(--gold); border: 1px solid #55402a; }}
            .btn-secondary:hover {{ background: #241b13; }}
            .overview {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; padding: 14px; margin: 18px 0; }}
            .overview-item {{ padding: 6px 13px; border-right: 1px solid var(--border); }}
            .overview-item:last-child {{ border: 0; }}
            .overview-item b {{ display: block; font-size: 22px; color: var(--text); }}
            .overview-item span {{ color: var(--muted); font-size: 11px; }}
            .overview-item.gold b {{ color: var(--gold); }} .overview-item.green b {{ color: var(--green); }}
            .overview-item.blue b {{ color: var(--blue); }} .overview-item.red b {{ color: var(--red); }}
            .section-head {{ display:flex; justify-content:space-between; align-items:center; gap: 12px; margin: 26px 0 12px; }}
            .section-head h2 {{ font-size: 17px; margin: 0; }} .section-head p {{ margin: 0; color: var(--muted); font-size: 12px; }}
            .employee-list {{ display: grid; gap: 10px; }}
            .employee-card {{ background: rgba(25, 20, 16, .95); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }}
            .employee-card[open] {{ border-color: #6d5426; }}
            .employee-card summary {{ cursor: pointer; list-style: none; display: flex; align-items: center; gap: 15px; padding: 16px 18px; }}
            .employee-card summary::-webkit-details-marker {{ display:none; }}
            .employee-main {{ min-width: 210px; display: flex; gap: 11px; align-items: center; flex: 1; }}
            .employee-main strong {{ display:block; font-size: 15px; color: var(--text); }}
            .employee-main small {{ display:block; color: var(--muted); font-size: 10px; margin-top: 5px; }}
            .employee-avatar {{ display:flex; align-items:center; justify-content:center; width: 36px; height:36px; border-radius: 10px; background: rgba(212,175,55,.13); color: var(--gold); font-weight: 800; }}
            .employee-metrics {{ display: flex; align-items: stretch; gap: 2px; }}
            .metric {{ min-width: 78px; padding: 0 13px; border-left: 1px solid var(--border); text-align: center; }}
            .metric b {{ display:block; font-size: 17px; color: var(--text); }} .metric em {{ color: var(--muted); font-size: 10px; font-style: normal; white-space: nowrap; }}
            .metric.incoming b {{ color: var(--green); }} .metric.outgoing b {{ color: var(--blue); }} .metric.missed b {{ color: var(--red); }} .metric.duration b {{ color: var(--gold); font-size: 13px; padding-top: 2px; }}
            .chevron {{ color: var(--gold); font-size: 22px; margin-left: 3px; transition: transform .2s; }} .employee-card[open] .chevron {{ transform: rotate(180deg); }}
            .employee-details {{ border-top: 1px solid var(--border); padding: 15px 18px 18px; background: rgba(13, 10, 8, .25); }}
            .calls-heading {{ display:flex; justify-content:space-between; color: var(--gold); font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 10px; }}
            .calls-heading span:last-child {{ color: var(--muted); font-weight: 500; text-transform: none; letter-spacing: 0; }}
            .calls-list {{ border: 1px solid var(--border); border-radius: 9px; overflow: hidden; }}
            .call-row {{ display:grid; grid-template-columns: 1.25fr 1.15fr .9fr .85fr 1.5fr; gap: 12px; align-items: center; padding: 12px 13px; border-top: 1px solid rgba(57,45,33,.7); font-size: 12px; }}
            .call-row:first-child {{ border-top: 0; }} .call-head {{ background: #211914; color: var(--gold); border: 0; font-size: 10px; text-transform: uppercase; font-weight: 800; }}
            .call-date, .call-number {{ color: #d8cdbd; }} .call-duration {{ color: var(--muted); }}
            .badge {{ display:inline-flex; padding: 4px 8px; border-radius: 5px; font-size: 11px; font-weight: 700; white-space: nowrap; }}
            .badge.incoming {{ color: var(--green); background: rgba(68,212,131,.1); }} .badge.outgoing {{ color: var(--blue); background: rgba(77,163,255,.1); }} .badge.missed {{ color: var(--red); background: rgba(239,108,99,.1); }} .badge.other {{ color: var(--muted); background: rgba(157,139,114,.1); }}
            .call-recording {{ display:flex; gap: 8px; align-items:center; min-width: 0; }} audio {{ height: 27px; max-width: 150px; }} .download-link {{ color:var(--gold); text-decoration:none; font-size:11px; border:1px solid #55402a; border-radius:5px; padding:5px 7px; white-space:nowrap; }} .no-recording {{ color:#66594d; font-size:11px; }}
            .empty-state {{ color: var(--muted); text-align: center; padding: 45px 20px; background: rgba(25,20,16,.9); border: 1px dashed var(--border); border-radius: 12px; }} .empty-state.compact {{ padding: 24px; border: 0; }}
            footer {{ text-align:center; color:#786237; font-size:11px; padding: 0 0 25px; }}
            @media (max-width: 900px) {{ .overview {{ grid-template-columns: repeat(3, 1fr); }} .employee-card summary {{ align-items:flex-start; flex-wrap:wrap; }} .employee-main {{ min-width: 45%; }} .employee-metrics {{ width:100%; overflow:auto; padding-left:47px; }} .chevron {{ position:absolute; right: 20px; }} .employee-card {{ position:relative; }} }}
            @media (max-width: 650px) {{ .container {{ width: calc(100% - 24px); padding-top: 22px; }} .header {{ display:block; }} .live-status {{ margin-top:15px; }} .overview {{ grid-template-columns: repeat(2, 1fr); }} .overview-item:nth-child(2) {{ border-right:0; }} .filters {{ padding: 13px; }} .filter-group {{ min-width: 100%; }} .filter-actions, .filter-actions .btn {{ width:100%; }} .call-head {{ display:none; }} .call-row {{ grid-template-columns: 1fr 1fr; gap: 8px; }} .call-recording {{ grid-column: 1 / -1; }} .metric {{ min-width: 70px; padding:0 9px; }} }}
        </style>
    </head>
    <body>
        <main class="container">
            <header class="header">
                <div>
                    <h1>IMAN SKIP/HARD CALL GROUP</h1>
                    <div class="sub-title">Журнал звонков · последние 48 часов</div>
                    <div class="signature">Architected &amp; Developed by Ravshanov</div>
                </div>
                <div class="live-status"><span class="live-dot"></span><span>Обновляется автоматически · 5 сек</span></div>
            </header>

            <section class="card">
                <form method="get" action="/" class="filters">
                    <div class="filter-group"><label for="employee">Сотрудник</label><select id="employee" name="employee">{employee_options}</select></div>
                    <div class="filter-group"><label for="date">Дата</label><input id="date" type="date" name="date" value="{escape(date or '', quote=True)}"></div>
                    <div class="filter-group"><label for="call_type">Тип звонка</label><select id="call_type" name="call_type">{type_options}</select></div>
                    <div class="filter-actions">
                        <button type="submit" class="btn">Применить</button>
                        <a href="/" class="btn btn-secondary">Сбросить</a>
                        <a href="/download-report" class="btn btn-secondary">Скачать Excel</a>
                    </div>
                </form>
            </section>

            <section class="card overview">
                <div class="overview-item gold"><b>{total_stats["total"]}</b><span>Всего звонков</span></div>
                <div class="overview-item green"><b>{total_stats["incoming"]}</b><span>Входящие</span></div>
                <div class="overview-item blue"><b>{total_stats["outgoing"]}</b><span>Исходящие</span></div>
                <div class="overview-item red"><b>{total_stats["missed"]}</b><span>Пропущенные</span></div>
                <div class="overview-item"><b>{escape(total_stats["duration"])}</b><span>Общая длительность</span></div>
            </section>

            <div class="section-head">
                <div><h2>Сотрудники</h2><p>Нажмите на сотрудника, чтобы раскрыть только его звонки</p></div>
                <p>Показано: {len(filtered_calls)} звонков</p>
            </div>
            <section class="employee-list">{cards_html}</section>
        </main>
        <footer>Architected &amp; Developed by Ravshanov &bull; IMAN Call Management System</footer>
        <script>
            (() => {{
                const refreshUrl = {json.dumps(refresh_url, ensure_ascii=False)};
                const opened = JSON.parse(sessionStorage.getItem("iman-open-employees") || "[]");
                document.querySelectorAll(".employee-card").forEach((card) => {{
                    if (opened.includes(card.dataset.employee)) card.open = true;
                }});
                setTimeout(() => {{
                    const openEmployees = [...document.querySelectorAll(".employee-card[open]")].map((card) => card.dataset.employee);
                    sessionStorage.setItem("iman-open-employees", JSON.stringify(openEmployees));
                    window.location.href = refreshUrl;
                }}, 5000);
            }})();
        </script>
    </body>
    </html>
    """
    return html_content


@app.get("/download-report")
async def download_report():
    calls = load_data()
    if not calls:
        raise HTTPException(status_code=404, detail="Нет данных")
    df = pd.DataFrame(calls)
    df = df[["datetime", "employee", "number", "type", "duration_formatted"]]
    df.columns = ["Дата и время", "Сотрудник", "Номер", "Тип", "Длительность"]
    df.to_excel("data/iman_call_report.xlsx", index=False)
    return FileResponse("data/iman_call_report.xlsx", filename="IMAN_Call_Report_48h.xlsx")
