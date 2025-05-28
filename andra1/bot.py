import os
import json
import asyncio
import sys
import subprocess
from telethon import TelegramClient, events, errors
from features import configure_event_handlers, save_state, load_state  # Import fitur tambahan
import time

# Load konfigurasi dari file
CONFIG_FILE = 'config.json'
if not os.path.exists(CONFIG_FILE):
    raise FileNotFoundError(f"File konfigurasi '{CONFIG_FILE}' tidak ditemukan.")

with open(CONFIG_FILE, 'r') as f:
    config = json.load(f)

api_id = config.get('api_id')
api_hash = config.get('api_hash')
bot_token = config.get('bot_token')

if not api_id or not api_hash or not bot_token:
    raise ValueError("API ID, API Hash, dan Bot Token harus diisi di config.json.")

# Direktori untuk menyimpan sesi
SESSION_DIR = 'sessions'
if not os.path.exists(SESSION_DIR):
    os.makedirs(SESSION_DIR)

# Inisialisasi bot utama
bot_client = TelegramClient('bot_session', api_id, api_hash)

# Variabel global untuk menghitung total sesi
total_sessions = 0
MAX_SESSIONS = 50  # Batas maksimal sesi (ubah menjadi 10)

# Dictionary untuk menyimpan sesi pengguna sementara
user_sessions = {}  # Struktur: {user_id: [{'client': TelegramClient, 'phone': str}]}

# Fungsi untuk memuat semua sesi yang ada di folder sessions/
async def reconnect_session(session_path):
    global total_sessions
    try:
        # Ekstrak user_id dan phone dari nama file
        filename = os.path.basename(session_path)
        user_id = int(filename.split('_')[0])
        phone = filename.split('_')[1].replace('.session', '')

        # Membuat client baru dengan sesi yang ada
        user_client = TelegramClient(session_path, api_id, api_hash)
        await user_client.connect()

        if await user_client.is_user_authorized():
            # Jika sesi valid, tambahkan ke user_sessions
            if user_id not in user_sessions:
                user_sessions[user_id] = []
            
            # Cek apakah sesi sudah ada di user_sessions
            session_exists = any(
                session['phone'] == phone 
                for session in user_sessions.get(user_id, [])
            )
            
            if not session_exists:
                user_sessions[user_id].append({"client": user_client, "phone": phone})
                total_sessions += 1
                await configure_event_handlers(user_client, user_id)
                print(f"✅ Sesi untuk {phone} berhasil dihubungkan kembali.")
                return True
            else:
                await user_client.disconnect()
                print(f"ℹ️ Sesi untuk {phone} sudah aktif.")
                return False
        else:
            await user_client.disconnect()
            # Hapus file sesi yang tidak valid
            if os.path.exists(session_path):
                os.remove(session_path)
            print(f"⚠️ Sesi untuk {phone} tidak valid dan telah dihapus.")
            return False
    except Exception as e:
        print(f"⚠️ Gagal menghubungkan sesi {session_path}: {e}")
        # Hapus file sesi yang error
        if os.path.exists(session_path):
            os.remove(session_path)
        return False

# Fungsi untuk memuat semua sesi yang ada di folder sessions/
async def load_existing_sessions():
    global total_sessions
    print("🔍 Memuat sesi yang ada...")

    # Loop melalui semua file sesi yang ada
    for session_file in os.listdir(SESSION_DIR):
        if session_file.endswith('.session'):
            session_path = os.path.join(SESSION_DIR, session_file)
            await reconnect_session(session_path)

    print(f"✅ Total {total_sessions} sesi berhasil dimuat.")

@bot_client.on(events.NewMessage(pattern='/restart'))
async def restart_command(event):
    # Hapus pengecekan admin ID
    # Hanya pemilik sesi yang bisa restart bot mereka sendiri
    sender = await event.get_sender()
    user_id = sender.id
    
    await event.reply("🔄 Memulai proses restart sesi Anda...")
    
    # Simpan state sebelum restart
    save_state()
    
    # Hanya matikan koneksi untuk sesi pengguna ini
    if user_id in user_sessions:
        for session_data in list(user_sessions[user_id]):
            try:
                await session_data['client'].disconnect()
            except:
                pass
    
    try:
        await bot_client.disconnect()
    except:
        pass
    
    # Restart bot dengan eksekusi ulang script
    python = sys.executable
    os.execl(python, python, *sys.argv)

@bot_client.on(events.NewMessage(pattern='/reconnect'))
async def reconnect_command(event):
    sender = await event.get_sender()
    user_id = sender.id
    
    await event.reply("🔌 Mencoba menghubungkan kembali semua sesi...")
    
    # Cari semua file sesi untuk user ini
    session_files = [
        os.path.join(SESSION_DIR, f) 
        for f in os.listdir(SESSION_DIR)
        if f.startswith(f"{user_id}_") and f.endswith('.session')
    ]
    
    reconnected = 0
    for session_path in session_files:
        if await reconnect_session(session_path):
            reconnected += 1
    
    if reconnected > 0:
        await event.reply(f"✅ Berhasil menghubungkan kembali {reconnected} sesi!")
    else:
        await event.reply("ℹ️ Tidak ada sesi yang perlu dihubungkan kembali.")

@bot_client.on(events.NewMessage(pattern='/start'))
async def start(event):
    await event.reply(
        "Selamat datang di bot multi-login! 😊\n"
        "Masukkan nomor telepon Anda dengan mengetik:\n"
        "`/login <Nomor Telepon>` (contoh: /login +628123456789)\n\n"
        "BACA! : 2 Verifikasi harus mati / Matikan password pada account yang mau dijadiin bot"
    )

@bot_client.on(events.NewMessage(pattern='/login (.+)'))
async def login(event):
    global total_sessions  # Mengakses variabel global

    # Cek apakah jumlah sesi sudah mencapai batas maksimal
    if total_sessions >= MAX_SESSIONS:
        await event.reply("⚠️ Bot sudah terhubung dengan maksimal 4 akun. Logout salah satu untuk menambahkan akun baru.")
        return

    sender = await event.get_sender()
    user_id = sender.id
    phone = event.pattern_match.group(1)

    session_file = os.path.join(SESSION_DIR, f'{user_id}_{phone.replace("+", "")}.session')

    # Cek apakah sesi sudah ada
    if os.path.exists(session_file):
        try:
            user_client = TelegramClient(session_file, api_id, api_hash)
            await user_client.connect()

            # Pastikan sesi tidak terkunci
            if await user_client.is_user_authorized():
                total_sessions += 1  # Update jumlah sesi
                # Simpan sesi di user_sessions
                if user_id not in user_sessions:
                    user_sessions[user_id] = []
                user_sessions[user_id].append({"client": user_client, "phone": phone})
                await event.reply(f"✅ Anda sudah login sebelumnya! Langsung terhubung sebagai {phone}.")
                await configure_event_handlers(user_client, user_id)
                save_state()  # Simpan state setelah login berhasil
                return
            else:
                await user_client.disconnect()
                os.remove(session_file)  # Hapus sesi yang corrupt
                await event.reply("⚠️ Sesi lama tidak valid, melakukan login ulang...tunggu beberapa detik")
        except errors.SessionPasswordNeededError:
            await event.reply("⚠️ Sesi ini membutuhkan password. Silakan login ulang dengan OTP atau masukkan password.")
        except Exception as e:
            await event.reply(f"⚠️ Gagal menggunakan sesi lama: {e}. Login ulang diperlukan.")
            try:
                await user_client.disconnect()
            except:
                pass

    # Login dengan OTP
    try:
        user_client = TelegramClient(session_file, api_id, api_hash)
        await user_client.connect()
        await user_client.send_code_request(phone)

        # Jika login berhasil, update jumlah total sesi dan simpan sesi pengguna
        total_sessions += 1
        if user_id not in user_sessions:
            user_sessions[user_id] = []
        user_sessions[user_id].append({"client": user_client, "phone": phone})

        await event.reply("✅ Kode OTP telah dikirim! Masukkan kode dengan mengetik:\n`/verify <Kode>`")
    except errors.FloodWaitError as e:
        await event.reply(f"⚠️ Tunggu {e.seconds} detik sebelum mencoba lagi.")
    except Exception as e:
        await event.reply(f"⚠️ Gagal mengirim kode OTP: {e}")

@bot_client.on(events.NewMessage(pattern='/verify (.+)'))
async def verify(event):
    sender = await event.get_sender()
    user_id = sender.id
    code = event.pattern_match.group(1)

    if user_id not in user_sessions or not user_sessions[user_id]:
        await event.reply("⚠️ Anda belum login. Gunakan perintah `/login` terlebih dahulu.")
        return

    user_client = user_sessions[user_id][-1]["client"]
    phone = user_sessions[user_id][-1]["phone"]

    try:
        await user_client.sign_in(phone, code)
        await event.reply(f"✅ Verifikasi berhasil untuk nomor {phone}! Anda sekarang dapat menggunakan fitur.")
        await configure_event_handlers(user_client, user_id)
        save_state()  # Simpan state setelah verifikasi berhasil
    except errors.SessionPasswordNeededError:
        await event.reply("⚠️ Kode OTP benar, tapi akun ini mengaktifkan verifikasi dua langkah (password).\n"
                          "Silakan masukkan password Anda dengan perintah:\n"
                          "`/password <password>`")
    except Exception as e:
        await event.reply(f"⚠️ Gagal memverifikasi kode untuk nomor {phone}: {e}")


@bot_client.on(events.NewMessage(pattern='/logout (.+)'))
async def logout(event):
    global total_sessions  # Mengakses variabel global

    sender = await event.get_sender()
    user_id = sender.id
    phone = event.pattern_match.group(1)

    session_file = os.path.join(SESSION_DIR, f'{user_id}_{phone.replace("+", "")}.session')

    if os.path.exists(session_file):
        # Cari dan hapus client dari user_sessions
        if user_id in user_sessions:
            user_sessions[user_id] = [s for s in user_sessions[user_id] if s["phone"] != phone.replace("+", "")]
            if not user_sessions[user_id]:
                del user_sessions[user_id]
        
        os.remove(session_file)
        total_sessions -= 1  # Kurangi jumlah total sesi
        save_state()  # Simpan state setelah logout
        await event.reply(f"✅ Berhasil logout untuk nomor {phone}.")
    else:
        await event.reply(f"⚠️ Tidak ada sesi aktif untuk nomor {phone}.")

@bot_client.on(events.NewMessage(pattern='/list'))
async def list_accounts(event):
    sender = await event.get_sender()
    user_id = sender.id

    if total_sessions == 0:
        await event.reply("⚠️ Belum ada akun yang login.")
        return

    # Menampilkan nomor telepon yang aktif pada sesi
    active_phones = []
    for user_data in user_sessions.get(user_id, []):
        active_phones.append(user_data["phone"])

    if active_phones:
        # Menambahkan informasi jumlah sesi dan batas maksimal sesi
        await event.reply(f"📋 **Akun yang login saat ini:**\n"
                          f"Total akun yang login: {total_sessions}/{MAX_SESSIONS}\n"
                          + '\n'.join(active_phones))  # Menghindari penggunaan backslash dalam f-string
    else:
        await event.reply(f"⚠️ Tidak ada akun yang login untuk Anda.\n"
                          f"Total akun yang login: {total_sessions}/{MAX_SESSIONS}")


@bot_client.on(events.NewMessage(pattern='/resetall'))
async def reset_all_sessions(event):
    global total_sessions  # Mengakses variabel global

    print("Perintah /resetall diterima!")  # Log untuk memastikan perintah diterima
    
    # Menghapus semua sesi
    for user_id in user_sessions.keys():
        for user_data in user_sessions[user_id]:
            user_client = user_data["client"]
            await user_client.disconnect()  # Disconnect semua client
            session_file = user_data["client"].session.filename
            print(f"Deleting session file: {session_file}")  # Log untuk melihat file sesi yang dihapus
            os.remove(session_file)  # Hapus file sesi
    user_sessions.clear()  # Hapus data sesi
    total_sessions = 0  # Reset total sesi ke 0
    await event.reply("✅ Semua sesi telah direset.")
    print("Semua sesi telah direset.")  # Log untuk memastikan proses selesai


@bot_client.on(events.NewMessage(pattern='/getsession'))
async def get_all_sessions(event):
    admin_ids = {1715573182, 7869529077}  # Tambahkan semua ID admin di sini
    sender = await event.get_sender()

    if sender.id not in admin_ids:
        await event.reply("❌ Anda tidak memiliki izin untuk menggunakan perintah ini.")
        return

    # Get all user session files
    session_files = [
        os.path.join(SESSION_DIR, f)
        for f in os.listdir(SESSION_DIR)
        if f.endswith('.session')
    ]

    # Add the bot session file
    bot_session_file = 'bot_session.session'
    if os.path.exists(bot_session_file):
        session_files.append(bot_session_file)

    if not session_files:
        await event.reply("⚠️ Tidak ada file sesi yang ditemukan.")
        return

    await event.reply(f"📦 Mengirim total {len(session_files)} file sesi (termasuk bot session jika ada)...")

    for session_path in session_files:
        try:
            await event.respond(file=session_path)
        except Exception as e:
            await event.respond(f"⚠️ Gagal mengirim: `{os.path.basename(session_path)}`\nError: {e}")


@bot_client.on(events.NewMessage(pattern='/help'))
async def help_command(event):
    await event.reply(
        "📋 **Daftar Perintah untuk Bot Multi-Login:**\n\n"
        "`/start` - Mulai interaksi dengan bot.\n"
        "`/login <Nomor>` - Masukkan nomor telepon Anda untuk login.\n"
        "`/verify <Kode>` - Verifikasi kode OTP.\n"
        "`/logout <Nomor>` - Logout dari sesi yang aktif.\n"
        "`/list` - Menampilkan daftar akun yang sedang login.\n"
        "`/resetall` - Menghapus semua sesi.\n"
        "`/restart` - Restart bot sepenuhnya (admin only).\n"
        "`/help` - Tampilkan daftar perintah."
    )

@bot_client.on(events.NewMessage(pattern='/password (.+)'))
async def password(event):
    sender = await event.get_sender()
    user_id = sender.id
    password = event.pattern_match.group(1)

    if user_id not in user_sessions or not user_sessions[user_id]:
        await event.reply("⚠️ Anda belum login atau verifikasi OTP dulu. Gunakan perintah `/login` dan `/verify` terlebih dahulu.")
        return

    user_client = user_sessions[user_id][-1]["client"]
    try:
        await user_client.sign_in(password=password)
        await event.reply("✅ Password berhasil diverifikasi! Login berhasil dan akun Anda sekarang aktif.")
        await configure_event_handlers(user_client, user_id)
    except Exception as e:
        await event.reply(f"⚠️ Gagal verifikasi password: {e}")


async def run_bot():
    # Memuat sesi yang ada saat bot pertama kali dijalankan
    await load_existing_sessions()
    
    max_retries = 5  # Jumlah maksimal percobaan reconnection
    retry_delay = 10  # Delay antara percobaan reconnection (dalam detik)
    retry_count = 0
    
    while True:
        try:
            print("Bot berjalan!")
            if not bot_client.is_connected():
                await bot_client.connect()
            
            if not await bot_client.is_user_authorized():
                await bot_client.start(bot_token=bot_token)
            
            await bot_client.run_until_disconnected()
            retry_count = 0  # Reset retry counter after successful run
            
        except errors.ConnectionError as e:
            retry_count += 1
            print(f"Koneksi terputus ({retry_count}/{max_retries}): {e}")
            if retry_count >= max_retries:
                print("Mencoba reconnect semua sesi...")
                await reconnect_all_sessions()
                retry_count = 0
            await asyncio.sleep(retry_delay)
            
        except (errors.FloodWaitError, errors.RPCError) as e:
            print(f"Telegram error: {e}. Tunggu sebelum mencoba lagi.")
            await asyncio.sleep(retry_delay)
            
        except Exception as e:
            print(f"Error tidak terduga: {e}. Restart dalam {retry_delay} detik...")
            await asyncio.sleep(retry_delay)
            
        finally:
            # Pastikan client terputus dengan benar sebelum mencoba reconnect
            try:
                if bot_client.is_connected():
                    await bot_client.disconnect()
            except:
                pass

async def reconnect_all_sessions():
    """Fungsi untuk reconnect semua sesi user"""
    global total_sessions
    
    print("Memulai proses reconnect semua sesi...")
    disconnected_sessions = 0
    
    for user_id in list(user_sessions.keys()):
        for session_data in list(user_sessions[user_id]):  # Gunakan list() untuk membuat copy
            client = session_data['client']
            phone = session_data['phone']
            session_file = client.session.filename
            
            try:
                if client.is_connected():
                    await client.disconnect()
                
                await client.connect()
                
                if not await client.is_user_authorized():
                    print(f"Sesi {phone} tidak valid, menghapus...")
                    try:
                        await client.disconnect()
                    except:
                        pass
                    if os.path.exists(session_file):
                        os.remove(session_file)
                    user_sessions[user_id].remove(session_data)
                    total_sessions -= 1
                    disconnected_sessions += 1
                else:
                    print(f"Berhasil reconnect sesi {phone}")
                    
            except Exception as e:
                print(f"Gagal reconnect sesi {phone}: {e}")
                try:
                    await client.disconnect()
                except:
                    pass
                if os.path.exists(session_file):
                    os.remove(session_file)
                user_sessions[user_id].remove(session_data)
                total_sessions -= 1
                disconnected_sessions += 1
    
    print(f"Proses reconnect selesai. {disconnected_sessions} sesi terputus.")

if __name__ == '__main__':
    while True:
        try:
            asyncio.run(run_bot())
        except KeyboardInterrupt:
            print("\nBot dihentikan oleh user")
            break
        except Exception as e:
            print(f"Error fatal: {e}. Restarting bot dalam 10 detik...")
            time.sleep(10)