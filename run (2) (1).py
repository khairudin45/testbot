import os
import requests
import tempfile
import subprocess
from threading import Thread
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from flask import Flask, request
import logging

# Konfigurasi logging
logging.basicConfig(level=logging.INFO)

# Variabel Lingkungan
API_ID = os.getenv("API_ID", "961780")
API_HASH = os.getenv("API_HASH", "bbbfa43f067e1e8e2fb41f334d32a6a7")
BOT_TOKEN = os.getenv("BOT_TOKEN", "1956338108:AAFj3Dt5KqX17PTbUWzh2lLRCyaXdpO3hDM")

# Inisialisasi bot Telegram
app = Client("deploy_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Inisialisasi Flask untuk fake website
web_app = Flask(__name__)

# Menyimpan informasi tentang subprocess yang berjalan
process_registry = {}

@web_app.route('/')
def home():
    return "Fake Website - Server is Running!"

# Menjalankan server Flask
def run_flask():
    port = int(os.getenv("PORT", 5000))  # Default ke 5000 jika tidak ada PORT di environment
    logging.info(f"Menjalankan Flask di port {port}")
    web_app.run(host="0.0.0.0", port=port, threaded=True)

# Endpoint Flask untuk mematikan server
@web_app.route('/shutdown', methods=['POST'])
def shutdown():
    func = request.environ.get('werkzeug.server.shutdown')
    if func:
        func()
        return "Server shutting down..."
    else:
        return "Shutdown function not available."

# Fungsi untuk deploy skrip dari URL atau file
@app.on_message(filters.command("deploy") | filters.document)
async def deploy(client: Client, message: Message):
    try:
        if message.document and message.document.file_name.endswith(".py"):
            # Jika file dikirim, gunakan file yang diunggah
            await message.reply("Menerima file skrip. Sedang mendownload...")
            file_path = await message.download()
        elif len(message.command) > 1:
            # Jika URL dikirim, unduh skrip dari URL
            url = message.command[1]
            await message.reply(f"Men-download skrip dari {url}...")
            try:
                response = requests.get(url)
                response.raise_for_status()
                with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as temp_file:
                    temp_file.write(response.content)
                    file_path = temp_file.name
            except requests.exceptions.RequestException as e:
                await message.reply(f"Gagal mendownload skrip: {e}")
                return
        else:
            await message.reply("Silakan berikan URL atau file skrip untuk dideploy!")
            return

        # Jalankan skrip dalam subprocess dan simpan log di file terpisah
        log_file_path = file_path + ".log"
        with open(log_file_path, "w") as log_file:
            process = subprocess.Popen(
                ['python3', file_path],
                stdout=log_file,
                stderr=log_file,
                env=os.environ
            )

        # Tambahkan ke registry
        process_registry[process.pid] = {
            "process": process,
            "file": file_path,
            "log": log_file_path,
            "status": "✅ Berjalan"
        }
        await message.reply(f"Skrip berhasil dijalankan dengan PID {process.pid}.")

        # Pantau proses untuk menangani crash
        monitor_process(client, process.pid, message.chat.id)

    except Exception as e:
        await message.reply(f"Terjadi kesalahan saat menjalankan skrip: {e}")

# Fungsi untuk memantau proses
def monitor_process(client: Client, pid: int, chat_id: int):
    def check():
        process_info = process_registry[pid]
        process = process_info["process"]
        return_code = process.poll()
        if return_code is not None:
            # Proses telah berhenti
            process_info["status"] = f"❌ Gagal (Kode: {return_code})"
            log_path = process_info["log"]
            error_message = f"Proses dengan PID {pid} telah berhenti secara tak terduga."
            if os.path.exists(log_path):
                with open(log_path, "r") as log_file:
                    error_logs = log_file.read()
                error_message += f"\n\nLog error:\n```\n{error_logs[-4000:]}\n```"
            else:
                error_message += "\nLog file tidak ditemukan."
            
            # Kirim pesan ke Telegram
            client.send_message(chat_id, error_message, parse_mode="markdown")
            # Coba restart ulang
            restart_process(pid, chat_id)
    
    # Jalankan di thread terpisah
    thread = Thread(target=check)
    thread.start()

# Fungsi untuk me-restart proses
def restart_process(pid: int, chat_id: int):
    process_info = process_registry.get(pid)
    if not process_info:
        return
    file_path = process_info["file"]
    log_file_path = process_info["log"]
    try:
        # Jalankan ulang proses
        with open(log_file_path, "w") as log_file:
            new_process = subprocess.Popen(
                ['python3', file_path],
                stdout=log_file,
                stderr=log_file,
                env=os.environ
            )
        process_registry[new_process.pid] = {
            "process": new_process,
            "file": file_path,
            "log": log_file_path,
            "status": "✅ Berjalan"
        }
        process_registry.pop(pid)  # Hapus proses lama
        app.send_message(chat_id, f"Proses dengan PID {pid} telah direstart. PID baru: {new_process.pid}.")
    except Exception as e:
        app.send_message(chat_id, f"Gagal me-restart proses PID {pid}: {e}")

# Fungsi untuk cek status semua proses
@app.on_message(filters.command("status"))
async def status(client: Client, message: Message):
    if not process_registry:
        await message.reply("Tidak ada skrip yang sedang berjalan.")
        return

    status_message = "Status Skrip yang Berjalan:\n"
    for pid, info in process_registry.items():
        if info["process"].poll() is not None:  # Proses telah berhenti
            info["status"] = "❌ Gagal"
        status_message += f"- PID {pid}: {info['status']} (File: {os.path.basename(info['file'])})\n"
    await message.reply(status_message)

# Fungsi untuk membaca log proses tertentu
@app.on_message(filters.command("log"))
async def log(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply("Gunakan: /log <PID>")
        return

    try:
        pid = int(message.command[1])
        if pid not in process_registry:
            await message.reply("PID tidak ditemukan.")
            return

        log_file_path = process_registry[pid]["log"]
        if os.path.exists(log_file_path):
            await message.reply_document(log_file_path, caption=f"Log untuk PID {pid}.")
        else:
            await message.reply("Log file tidak ditemukan.")
    except ValueError:
        await message.reply("PID harus berupa angka.")

# Fungsi untuk menghentikan proses tertentu
@app.on_message(filters.command("stop"))
async def stop(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply("Gunakan: /stop <PID>")
        return

    try:
        pid = int(message.command[1])
        if pid not in process_registry:
            await message.reply("PID tidak ditemukan.")
            return

        process_info = process_registry[pid]
        if process_info["process"].poll() is None:  # Proses masih berjalan
            process_info["process"].terminate()
            process_info["process"].wait()
            os.remove(process_info["file"])  # Hapus file sementara
            os.remove(process_info["log"])  # Hapus file log
            del process_registry[pid]
            await message.reply(f"Proses dengan PID {pid} berhasil dihentikan.")
        else:
            await message.reply("Proses telah berhenti.")
    except ValueError:
        await message.reply("PID harus berupa angka.")

# Fungsi utama untuk menjalankan bot dan web server
if __name__ == "__main__":
    print("Bot dan Web Server sedang berjalan...")
    try:
        # Jalankan server Flask di thread terpisah
        thread = Thread(target=run_flask)
        thread.start()
        
        # Mulai bot Pyrogram
        app.start()

        # Pastikan bot tetap berjalan
        idle()  # Menjaga bot tetap berjalan

    except KeyboardInterrupt:
        print("Menutup aplikasi...")
    finally:
        app.stop()
