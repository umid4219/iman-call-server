from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timedelta
import os
import json
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
    except ValueError:
        sec = 0
    if sec == 0: return "0 сек"
    minutes = sec // 60
    remaining_sec = sec % 60
    if minutes == 0: return f"{remaining_sec} сек"
    return f"{minutes} мин {remaining_sec} сек"

@app.post("/api/upload-call")
async def upload_call(
    employee: str = Form("Сотрудник"),
    number: str = Form(None),
    duration: str = Form("0"),
    call_type: str = Form("Пропущенный"),
    date_timestamp: int = Form(None),
    file: UploadFile = File(None)
):
    try:
        # Добавляем 5 часов к текущему серверному времени (UTC -> UTC+5)
        local_now = datetime.utcnow() + timedelta(hours=5)
        current_time_str = local_now.strftime('%Y-%m-%d %H:%M:%S')
        
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

        try:
            duration_sec = int(float(duration))
        except ValueError:
            duration_sec = 0

        # Корректируем время звонка с телефона с учетом +5 часов
        call_dt = datetime.utcfromtimestamp(date_timestamp / 1000) + timedelta(hours=5)

        calls = load_data()
        new_call = {
            "employee": employee,
            "number": number,
            "duration_seconds": duration_sec,
            "duration_formatted": format_duration(duration),
            "type": call_type,
            "timestamp": date_timestamp,
            "datetime": call_dt.strftime('%Y-%m-%d %H:%M:%S'),
            "audio_url": file_path
        }
        
        exists = any(c['employee'] == employee and c['timestamp'] == date_timestamp and c['number'] == number for c in calls)
        if not exists:
            calls.insert(0, new_call)
        
        cutoff_time = (datetime.utcnow() + timedelta(hours=5) - timedelta(hours=48)).timestamp() * 1000
        calls = [c for c in calls if c["timestamp"] >= cutoff_time]
        
        save_data(calls)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def filter_calls(calls, employee, date, call_type, min_duration):
    filtered = calls
    if employee != "all":
        filtered = [c for c in filtered if c['employee'] == employee]
    if date:
        filtered = [c for c in filtered if c['datetime'].startswith(date)]
    if call_type != "all":
        filtered = [c for c in filtered if c['type'].lower() == call_type.lower()]
    if min_duration > 0:
        filtered = [c for c in filtered if c.get('duration_seconds', 0) >= min_duration]
    return filtered

@app.get("/", response_class=HTMLResponse)
async def get_dashboard(employee: str = "all", date: str = None, call_type: str = "all", min_duration: int = 0):
    calls = load_data()
    sync_status = load_sync_status()
    employees = sorted(list(set(c['employee'] for c in calls).union(sync_status.keys())))
    
    filtered_calls = filter_calls(calls, employee, date, call_type, min_duration)

    rows_html = "".join([f"""
        <tr>
            <td>{c['datetime']}</td>
            <td><strong>{c['employee']}</strong></td>
            <td>{c['number']}</td>
            <td><span class="badge {c['type']}">{c['type']}</span></td>
            <td>{c['duration_formatted']}</td>
            <td>
                {'<div style="display: flex; align-items: center; gap: 12px;"><audio controls src="'+c["audio_url"]+'" style="height:28px;"></audio>' + 
                 '<a href="'+c["audio_url"]+'" download class="download-link">Скачать</a></div>' 
                 if c["audio_url"] else '<span style="color:#666;">Нет записи</span>'}
            </td>
        </tr>
    """ for c in filtered_calls])

    emp_options = '<option value="all">Все сотрудники</option>'
    for emp in employees:
        last_seen = sync_status.get(emp, "Нет данных")
        selected = 'selected' if emp == employee else ''
        emp_options += f'<option value="{emp}" {selected}>{emp} (Связь: {last_seen})</option>'

    ct_all = 'selected' if call_type == 'all' else ''
    ct_in = 'selected' if call_type == 'Входящий' else ''
    ct_out = 'selected' if call_type == 'Исходящий' else ''
    ct_miss = 'selected' if call_type == 'Пропущенный' else ''

    dur_0 = 'selected' if min_duration == 0 else ''
    dur_10 = 'selected' if min_duration == 10 else ''
    dur_30 = 'selected' if min_duration == 30 else ''
    dur_60 = 'selected' if min_duration == 60 else ''

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>IMAN - Monitoring</title>
        <style>
            body {{ background: linear-gradient(135deg, #12100e 0%, #1a1410 50%, #0d0b09 100%); background-attachment: fixed; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; margin: 0; padding: 30px; min-height: 100vh; display: flex; flex-direction: column; }}
            .container {{ max-width: 1300px; margin: 0 auto; width: 100%; flex: 1; }}
            .header-box {{ margin-bottom: 30px; }}
            h1 {{ color: #d4af37; margin: 0; font-size: 32px; letter-spacing: 1px; text-transform: uppercase; }}
            .sub-title {{ color: #b8860b; font-size: 16px; margin: 5px 0 0 0; font-weight: 400; }}
            .signature {{ color: #8b6508; font-size: 12px; margin-top: 5px; font-style: italic; letter-spacing: 0.5px; }}
            .card {{ background: rgba(26, 22, 19, 0.85); backdrop-filter: blur(10px); padding: 25px; border-radius: 12px; margin-bottom: 25px; border: 1px solid #3d3124; }}
            footer {{ text-align: center; padding: 40px 0 20px 0; color: #a68930; font-size: 12px; font-style: italic; opacity: 0.8; letter-spacing: 0.5px; }}
            .filters {{ display: flex; gap: 15px; align-items: center; flex-wrap: wrap; }}
            .filter-group {{ display: flex; flex-direction: column; gap: 5px; }}
            .filter-group label {{ font-size: 11px; color: #d4af37; font-weight: bold; }}
            select, input[type="date"] {{ background: #181310; color: #d4af37; border: 1px solid #4a3b2c; padding: 10px 15px; border-radius: 6px; font-size: 14px; }}
            .btn {{ background: #d4af37; color: #12100e; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: bold; text-decoration: none; display: inline-flex; align-items: center; justify-content: center; }}
            .btn-secondary {{ background: #181310; color: #d4af37; border: 1px solid #4a3b2c; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            th, td {{ padding: 14px 16px; text-align: left; border-bottom: 1px solid #2a221b; font-size: 14px; }}
            th {{ background-color: #1f1814; color: #d4af37; font-size: 11px; text-transform: uppercase; }}
            .badge {{ padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: bold; }}
            .badge.Входящий {{ color: #2ecc71; border: 1px solid rgba(46,204,113,0.3); }}
            .badge.Исходящий {{ color: #3498db; border: 1px solid rgba(52,152,219,0.3); }}
            .badge.Пропущенный {{ color: #e74c3c; border: 1px solid rgba(231,76,60,0.3); }}
            .download-link {{ color: #d4af37; text-decoration: none; font-size: 12px; font-weight: bold; padding: 4px 8px; border: 1px solid #4a3b2c; border-radius: 4px; background: #181310; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header-box">
                <h1>IMAN SKIP/HARD CALL GROUP</h1>
                <div class="sub-title">Журнал звонков (48 часов) с контролем фоновой связи</div>
                <div class="signature">Architected & Developed by Ravshanov</div>
            </div>
            
            <div class="card">
                <form method="get" action="/" class="filters">
                    <div class="filter-group"><label>СОТРУДНИК</label><select name="employee">{emp_options}</select></div>
                    <div class="filter-group"><label>ДАТА</label><input type="date" name="date" value="{date if date else ''}"></div>
                    <div class="filter-group">
                        <label>ТИП ЗВОНКА</label>
                        <select name="call_type">
                            <option value="all" {ct_all}>Все типы</option>
                            <option value="Входящий" {ct_in}>Входящие</option>
                            <option value="Исходящий" {ct_out}>Исходящие</option>
                            <option value="Пропущенный" {ct_miss}>Пропущенные</option>
                        </select>
                    </div>
                    <div class="filter-group">
                        <label>МИН. ДЛИТЕЛЬНОСТЬ</label>
                        <select name="min_duration">
                            любые <option value="0" {dur_0}>Любая</option>
                            <option value="10" {dur_10}>От 10 сек</option>
                            <option value="30" {dur_30}>От 30 сек</option>
                            <option value="60" {dur_60}>От 1 мин</option>
                        </select>
                    </div>
                    <div style="display: flex; gap: 10px; align-items: flex-end; margin-top: 18px;">
                        <button type="submit" class="btn">Применить</button>
                        <a href="/" class="btn btn-secondary">Сброс</a>
                        <a href="/download-report?employee={employee}&date={date if date else ''}&call_type={call_type}&min_duration={min_duration}" class="btn" style="margin-left: 15px;">Скачать Excel</a>
                    </div>
                </form>
            </div>
            
            <div class="card">
                <table>
                    <thead><tr><th>Дата / Время</th><th>Сотрудник</th><th>Номер</th><th>Тип</th><th>Длительность</th><th>Запись</th></tr></thead>
                    <tbody>{rows_html if rows_html else '<tr><td colspan="6" style="text-align:center; color: #77685b; padding: 40px;">Нет данных, соответствующих выбранным фильтрам</td></tr>'}</tbody>
                </table>
            </div>
        </div>

        <footer>
            Architected & Developed by Ravshanov &bull; IMAN Call Management System
        </footer>
    </body>
    </html>
    """
    return html_content

@app.get("/download-report")
async def download_report(employee: str = "all", date: str = None, call_type: str = "all", min_duration: int = 0):
    calls = load_data()
    filtered_calls = filter_calls(calls, employee, date, call_type, min_duration)
    
    if not filtered_calls: 
        raise HTTPException(status_code=404, detail="Нет данных по выбранным фильтрам")
    
    df = pd.DataFrame(filtered_calls)
    df = df[['datetime', 'employee', 'number', 'type', 'duration_formatted']]
    df.columns = ['Дата и время', 'Сотрудник', 'Номер', 'Тип', 'Длительность']
    df.to_excel("data/iman_call_report.xlsx", index=False)
    return FileResponse("data/iman_call_report.xlsx", filename="IMAN_Call_Report_Filtered.xlsx")
