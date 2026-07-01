#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Mac 电脑资产采集工具
可直接双击 .app 运行
"""

import sys
import os
import json
import socket
import getpass
import threading
import traceback
import platform
import subprocess
import re
from datetime import datetime

# 确保在 macOS 上运行时能找到依赖
try:
    import psutil
    import mysql.connector
except ImportError:
    import subprocess
    import sys

    # 自动安装依赖
    subprocess.check_call([sys.executable, "-m", "pip", "install", "psutil", "mysql-connector-python"])
    import psutil
    import mysql.connector

import tkinter as tk
from tkinter import messagebox, ttk

# =========================
# 基础配置
# =========================

APP_NAME = "公司电脑资产采集工具"
APP_VERSION = "v1.1.0"

# MySQL 配置
DB_CONFIG = {
    "host": "rm-bp1pk2rf9z16l9x7aio.mysql.rds.aliyuncs.com",
    "port": 3306,
    "user": "mmdy_test",
    "password": "mmdytest@1",
    "database": "mmdy_db",
    "charset": "utf8mb4"
}

TABLE_NAME = "computer_assets"


# =========================
# 工具函数
# =========================

def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def bytes_to_gb(value):
    try:
        num = float(value)
        if num < 0:
            return 0
        return round(num / 1024 / 1024 / 1024, 2)
    except Exception:
        return 0


def normalize_serial_number(value):
    serial = clean_text(value)
    invalid_values = {
        "", "0", "NONE", "NULL", "UNKNOWN", "DEFAULT STRING",
        "SYSTEM SERIAL NUMBER", "TO BE FILLED BY O.E.M.",
        "TO BE FILLED BY OEM", "TO BE FILLED BY O.E.M",
        "NOT APPLICABLE", "N/A", "Not Available", "System Serial#",
        "System Serial Number", "Serial Number"
    }
    if serial.upper() in invalid_values:
        return None
    return serial


def run_command(command):
    """执行 shell 命令并返回输出"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.stdout.strip()
    except Exception:
        return ""


def get_os_version():
    version = run_command("sw_vers -productVersion")
    return version


# =========================
# macOS 硬件信息采集
# =========================

def collect_macos_info():
    data = {
        "manufacturer": "Apple",
        "model": "",
        "serial_number": None,
        "os_caption": "macOS",
        "os_version": "",
        "os_architecture": "",
        "cpu": "",
        "cpu_cores": None,
        "cpu_threads": None,
        "ram_total_gb": 0,
        "ram_slots": [],
        "physical_disks": [],
        "gpu_info": []
    }

    data["os_version"] = get_os_version()
    arch = run_command("uname -m")
    data["os_architecture"] = arch if arch else "Unknown"

    hw = run_command("system_profiler SPHardwareDataType")

    for line in hw.split("\n"):
        line = line.strip()
        if "Model Name" in line:
            data["model"] = line.split(":", 1)[-1].strip()
        elif "Model Identifier" in line and not data["model"]:
            data["model"] = line.split(":", 1)[-1].strip()
        elif "Serial Number (system)" in line:
            sn = line.split(":", 1)[-1].strip()
            data["serial_number"] = normalize_serial_number(sn)
        elif "Serial Number" in line and "system" not in line.lower():
            sn = line.split(":", 1)[-1].strip()
            if not data["serial_number"]:
                data["serial_number"] = normalize_serial_number(sn)
        elif "Processor Name" in line:
            data["cpu"] = line.split(":", 1)[-1].strip()
        elif "Processor Speed" in line and not data["cpu"]:
            data["cpu"] += " " + line.split(":", 1)[-1].strip()
        elif "Number of Cores" in line:
            match = re.search(r"(\d+)", line)
            if match:
                data["cpu_cores"] = int(match.group(1))
        elif "Total Number of Cores" in line:
            match = re.search(r"(\d+)", line)
            if match:
                data["cpu_cores"] = int(match.group(1))
        elif "Memory" in line:
            match = re.search(r"(\d+)\s*GB", line)
            if match:
                data["ram_total_gb"] = float(match.group(1))

    if not data["serial_number"]:
        ioreg_sn = run_command("ioreg -l | grep IOPlatformSerialNumber | awk '{print $4}' | tr -d '\"'")
        if ioreg_sn:
            data["serial_number"] = normalize_serial_number(ioreg_sn)

    if not data["cpu"]:
        cpu_brand = run_command("sysctl -n machdep.cpu.brand_string")
        if cpu_brand:
            data["cpu"] = cpu_brand

    if data["cpu_cores"] is None:
        cores = run_command("sysctl -n hw.physicalcpu")
        if cores and cores.isdigit():
            data["cpu_cores"] = int(cores)

    if data["cpu_threads"] is None:
        threads = run_command("sysctl -n hw.logicalcpu")
        if threads and threads.isdigit():
            data["cpu_threads"] = int(threads)

    if data["ram_total_gb"] == 0:
        mem_bytes = run_command("sysctl -n hw.memsize")
        if mem_bytes and mem_bytes.isdigit():
            data["ram_total_gb"] = bytes_to_gb(int(mem_bytes))

    mem_info = run_command("system_profiler SPMemoryDataType")
    current_slot = {}
    for line in mem_info.split("\n"):
        line = line.strip()
        if "Bank:" in line or "Slot:" in line:
            if current_slot and "capacity_gb" in current_slot:
                data["ram_slots"].append(current_slot)
            current_slot = {"manufacturer": "Apple", "serial_number": "N/A"}
        elif "Size" in line and ":" in line:
            size = line.split(":", 1)[-1].strip()
            match = re.search(r"(\d+)\s*GB", size)
            if match:
                current_slot["capacity_gb"] = float(match.group(1))
        elif "Type" in line and ":" in line:
            current_slot["part_number"] = line.split(":", 1)[-1].strip()
        elif "Speed" in line and ":" in line:
            current_slot["speed_mhz"] = line.split(":", 1)[-1].strip()
        elif "Serial Number" in line and ":" in line:
            sn = line.split(":", 1)[-1].strip()
            if sn and sn != "N/A":
                current_slot["serial_number"] = sn
    if current_slot and "capacity_gb" in current_slot:
        data["ram_slots"].append(current_slot)

    if not data["ram_slots"] and data["ram_total_gb"] > 0:
        data["ram_slots"].append({
            "manufacturer": "Apple",
            "part_number": "Built-in",
            "serial_number": "N/A",
            "capacity_gb": data["ram_total_gb"],
            "speed_mhz": ""
        })

    storage = run_command("system_profiler SPStorageDataType")
    current_disk = {}
    for line in storage.split("\n"):
        line = line.strip()
        if "Media Name" in line:
            if current_disk and "size_gb" in current_disk:
                data["physical_disks"].append(current_disk)
            current_disk = {
                "model": line.split(":", 1)[-1].strip(),
                "interface_type": "Unknown",
                "media_type": "SSD",
                "serial_number": "N/A"
            }
        elif "Protocol" in line and ":" in line:
            current_disk["interface_type"] = line.split(":", 1)[-1].strip()
        elif "Medium Type" in line and ":" in line:
            current_disk["media_type"] = line.split(":", 1)[-1].strip()
        elif "Capacity" in line and ":" in line:
            cap = line.split(":", 1)[-1].strip()
            match = re.search(r"([\d.]+)\s*(TB|GB)", cap)
            if match:
                num = float(match.group(1))
                unit = match.group(2)
                current_disk["size_gb"] = num * 1000 if unit == "TB" else num
        elif "Device Serial" in line and ":" in line:
            current_disk["serial_number"] = line.split(":", 1)[-1].strip()
    if current_disk and "size_gb" in current_disk:
        data["physical_disks"].append(current_disk)

    gpu = run_command("system_profiler SPDisplaysDataType")
    current_gpu = {}
    for line in gpu.split("\n"):
        line = line.strip()
        if "Chipset Model" in line:
            if current_gpu and "name" in current_gpu:
                data["gpu_info"].append(current_gpu)
            current_gpu = {
                "name": line.split(":", 1)[-1].strip(),
                "driver_version": "",
                "video_processor": "",
                "adapter_ram_gb": 0
            }
        elif "Graphics/Displays" in line and not current_gpu.get("name"):
            current_gpu = {
                "name": line.split(":", 1)[-1].strip(),
                "driver_version": "",
                "video_processor": "",
                "adapter_ram_gb": 0
            }
        elif "VRAM (Total)" in line or "VRAM" in line:
            match = re.search(r"(\d+)\s*GB", line)
            if match:
                current_gpu["adapter_ram_gb"] = float(match.group(1))
        elif "Version" in line and ":" in line and not current_gpu.get("driver_version"):
            current_gpu["driver_version"] = line.split(":", 1)[-1].strip()
    if current_gpu and "name" in current_gpu:
        data["gpu_info"].append(current_gpu)

    return data


def collect_inventory(employee_name, department, remark):
    mac_info = collect_macos_info()
    data = {
        "employee_name": employee_name,
        "department": department,
        "remark": remark,
        "hostname": socket.gethostname(),
        "login_user": getpass.getuser(),
        "manufacturer": mac_info.get("manufacturer", "Apple"),
        "model": mac_info.get("model", ""),
        "serial_number": mac_info.get("serial_number"),
        "os_caption": mac_info.get("os_caption", "macOS"),
        "os_version": mac_info.get("os_version", ""),
        "os_architecture": mac_info.get("os_architecture", ""),
        "cpu": mac_info.get("cpu", ""),
        "cpu_cores": mac_info.get("cpu_cores"),
        "cpu_threads": mac_info.get("cpu_threads"),
        "ram_total_gb": mac_info.get("ram_total_gb", 0),
        "ram_slots": mac_info.get("ram_slots", []),
        "physical_disks": mac_info.get("physical_disks", []),
        "gpu_info": mac_info.get("gpu_info", []),
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    return data


def insert_or_update_mysql(data):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()

        sql = f"""
        INSERT INTO {TABLE_NAME} (
            employee_name, department, remark,
            hostname, login_user,
            manufacturer, model, serial_number,
            os_caption, os_version, os_architecture,
            cpu, cpu_cores, cpu_threads,
            ram_total_gb,
            ram_slots, physical_disks, gpu_info,
            collected_at
        ) VALUES (
            %(employee_name)s, %(department)s, %(remark)s,
            %(hostname)s, %(login_user)s,
            %(manufacturer)s, %(model)s, %(serial_number)s,
            %(os_caption)s, %(os_version)s, %(os_architecture)s,
            %(cpu)s, %(cpu_cores)s, %(cpu_threads)s,
            %(ram_total_gb)s,
            %(ram_slots)s, %(physical_disks)s, %(gpu_info)s,
            %(collected_at)s
        )
        ON DUPLICATE KEY UPDATE
            remark = VALUES(remark),
            hostname = VALUES(hostname),
            login_user = VALUES(login_user),
            manufacturer = VALUES(manufacturer),
            model = VALUES(model),
            serial_number = VALUES(serial_number),
            os_caption = VALUES(os_caption),
            os_version = VALUES(os_version),
            os_architecture = VALUES(os_architecture),
            cpu = VALUES(cpu),
            cpu_cores = VALUES(cpu_cores),
            cpu_threads = VALUES(cpu_threads),
            ram_total_gb = VALUES(ram_total_gb),
            ram_slots = VALUES(ram_slots),
            physical_disks = VALUES(physical_disks),
            gpu_info = VALUES(gpu_info),
            collected_at = VALUES(collected_at),
            updated_at = CURRENT_TIMESTAMP
        """

        payload = {
            "employee_name": data.get("employee_name"),
            "department": data.get("department"),
            "remark": data.get("remark"),
            "hostname": data.get("hostname"),
            "login_user": data.get("login_user"),
            "manufacturer": data.get("manufacturer"),
            "model": data.get("model"),
            "serial_number": data.get("serial_number"),
            "os_caption": data.get("os_caption"),
            "os_version": data.get("os_version"),
            "os_architecture": data.get("os_architecture"),
            "cpu": data.get("cpu"),
            "cpu_cores": data.get("cpu_cores"),
            "cpu_threads": data.get("cpu_threads"),
            "ram_total_gb": data.get("ram_total_gb"),
            "ram_slots": json.dumps(data.get("ram_slots", []), ensure_ascii=False),
            "physical_disks": json.dumps(data.get("physical_disks", []), ensure_ascii=False),
            "gpu_info": json.dumps(data.get("gpu_info", []), ensure_ascii=False),
            "collected_at": data.get("collected_at")
        }

        cursor.execute(sql, payload)
        conn.commit()
    except mysql.connector.Error as e:
        raise Exception(f"数据库错误: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# =========================
# GUI 界面
# =========================

def center_window(window, width, height):
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    x = int((screen_width - width) / 2)
    y = int((screen_height - height) / 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


def set_loading(is_loading):
    if is_loading:
        submit_button.config(state="disabled", text="⏳ 正在采集...")
        status_var.set("正在采集并写入数据库，请不要关闭窗口。")
    else:
        submit_button.config(state="normal", text="🚀 开始采集")
        status_var.set("准备就绪")


def submit_form():
    employee_name = name_var.get().strip()
    department = department_var.get().strip()
    remark = remark_var.get().strip()

    if not employee_name:
        messagebox.showwarning("提示", "请输入姓名。")
        name_entry.focus()
        return

    if not department:
        messagebox.showwarning("提示", "请输入部门。")
        department_entry.focus()
        return

    set_loading(True)

    def worker():
        try:
            data = collect_inventory(employee_name, department, remark)
            insert_or_update_mysql(data)

            def success():
                set_loading(False)
                messagebox.showinfo("✅ 提交成功", "电脑资产信息已提交成功。")
                status_var.set("提交成功 ✅")

            root.after(0, success)

        except Exception as err:
            error_message = str(err)

            def failed():
                set_loading(False)
                msg = f"提交失败，请联系 IT 管理员。\n\n错误信息：\n{error_message}"
                messagebox.showerror("❌ 提交失败", msg)
                status_var.set("提交失败 ❌")

            root.after(0, failed)

    threading.Thread(target=worker, daemon=True).start()


def show_info_dialog():
    """启动时显示 Mac 信息确认对话框"""
    dialog = tk.Toplevel(root)
    dialog.title("🍎 Mac 信息确认")
    dialog.resizable(False, False)
    dialog.transient(root)
    dialog.grab_set()

    width = 440
    height = 280
    center_window(dialog, width, height)

    mac_info = collect_macos_info()

    tk.Label(
        dialog,
        text="🍎 Mac 电脑资产采集",
        font=("Helvetica", 18, "bold")
    ).pack(pady=(20, 8))

    tk.Label(
        dialog,
        text="检测到以下 Mac 信息，请确认：",
        font=("Helvetica", 11),
        fg="#555555"
    ).pack(pady=(0, 12))

    info_text = f"""
    型号：{mac_info.get('model', '未知')}
    序列号：{mac_info.get('serial_number', '未知')}
    系统：macOS {mac_info.get('os_version', '未知')}
    CPU：{mac_info.get('cpu', '未知')}
    内存：{mac_info.get('ram_total_gb', 0)} GB
    """

    tk.Label(
        dialog,
        text=info_text,
        font=("Helvetica", 11),
        fg="#333333",
        justify="left"
    ).pack(pady=(0, 12))

    tk.Label(
        dialog,
        text="确认后点击「开始采集」进入主界面",
        font=("Helvetica", 9),
        fg="#999999"
    ).pack(pady=(0, 10))

    def on_confirm():
        dialog.destroy()
        info_label.config(text=f"🍎 {mac_info.get('model', 'Mac')} | SN: {mac_info.get('serial_number', 'N/A')}")

    btn_frame = tk.Frame(dialog)
    btn_frame.pack(pady=(0, 20))

    tk.Button(
        btn_frame,
        text="✅ 确认，开始采集",
        font=("Helvetica", 12, "bold"),
        width=16,
        bg="#0078D7",
        fg="white",
        activebackground="#005A9E",
        activeforeground="white",
        cursor="hand2",
        command=on_confirm
    ).pack(side=tk.LEFT, padx=10)

    dialog.bind("<Return>", lambda e: on_confirm())
    dialog.wait_window()


# =========================
# 创建主窗口
# =========================

root = tk.Tk()
root.title(f"{APP_NAME} {APP_VERSION}")
center_window(root, 480, 460)
root.resizable(False, False)

# 设置窗口图标（使用系统默认）
try:
    root.tk.call('wm', 'iconphoto', root._w, tk.PhotoImage(
        file='/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/GenericApplicationIcon.icns'))
except:
    pass

name_var = tk.StringVar()
department_var = tk.StringVar()
remark_var = tk.StringVar()
status_var = tk.StringVar(value="准备就绪")

# 标题
title = tk.Label(
    root,
    text="🍎 公司电脑资产采集",
    font=("Helvetica", 20, "bold")
)
title.pack(pady=(25, 8))

# Mac 信息
info_label = tk.Label(
    root,
    text="正在检测 Mac 信息...",
    font=("Helvetica", 11),
    fg="#0078D7"
)
info_label.pack(pady=(0, 8))

# 描述
desc = tk.Label(
    root,
    text="请输入姓名和部门，程序将自动采集本机资产信息",
    font=("Helvetica", 11),
    fg="#555555"
)
desc.pack(pady=(0, 18))

# 表单
form = tk.Frame(root)
form.pack(pady=4)

label_font = ("Helvetica", 12)
entry_font = ("Helvetica", 12)

tk.Label(form, text="姓名：", font=label_font).grid(row=0, column=0, padx=10, pady=10, sticky="e")
name_entry = tk.Entry(form, textvariable=name_var, font=entry_font, width=28)
name_entry.grid(row=0, column=1, padx=10, pady=10)

tk.Label(form, text="部门：", font=label_font).grid(row=1, column=0, padx=10, pady=10, sticky="e")
department_entry = tk.Entry(form, textvariable=department_var, font=entry_font, width=28)
department_entry.grid(row=1, column=1, padx=10, pady=10)

tk.Label(form, text="备注：", font=label_font).grid(row=2, column=0, padx=10, pady=10, sticky="e")
remark_entry = tk.Entry(form, textvariable=remark_var, font=entry_font, width=28)
remark_entry.grid(row=2, column=1, padx=10, pady=10)

# 提交按钮
submit_button = tk.Button(
    root,
    text="🚀 开始采集",
    font=("Helvetica", 13, "bold"),
    width=18,
    height=1,
    bg="#0078D7",
    fg="white",
    activebackground="#005A9E",
    activeforeground="white",
    cursor="hand2",
    command=submit_form
)
submit_button.pack(pady=(20, 10))

# 状态栏
status_label = tk.Label(
    root,
    textvariable=status_var,
    font=("Helvetica", 10),
    fg="#777777"
)
status_label.pack(pady=(2, 0))

# 底部说明
footer = tk.Label(
    root,
    text="仅采集：型号、序列号、系统、CPU、内存、硬盘、显卡等资产信息",
    font=("Helvetica", 9),
    fg="#999999"
)
footer.pack(pady=(12, 0))

name_entry.focus()

# 启动后显示确认对话框
root.after(300, show_info_dialog)

root.mainloop()