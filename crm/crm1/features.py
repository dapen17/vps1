import asyncio
import re
import logging
import json
import os
from telethon import events, errors
from telethon.tl.types import InputPeerUser
from datetime import datetime
from collections import defaultdict

# Menonaktifkan logging Telethon
logging.basicConfig(level=logging.CRITICAL)

# File untuk menyimpan state
STATE_FILE = 'bot_state.json'

# Menyimpan status per akun dan grup
active_groups = defaultdict(lambda: defaultdict(bool))  # {group_id: {user_id: status}}
active_bc_interval = defaultdict(lambda: defaultdict(bool))  # {user_id: {type: status}}
broadcast_data = defaultdict(dict)  # {user_id: {bc_type: {'message': str, 'interval': int}}}
blacklist = set()
auto_replies = defaultdict(str)  # {user_id: auto_reply_message}

def parse_interval(interval_str):
    """Konversi format [10s, 1m, 2h, 1d] menjadi detik."""
    match = re.match(r'^(\d+)([smhd])$', interval_str)
    if not match:
        return None
    value, unit = match.groups()
    value = int(value)
    return value * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[unit]

def get_today_date():
    """Mengembalikan tanggal hari ini dalam format YYYY-MM-DD."""
    return datetime.now().strftime("%Y-%m-%d")

def save_state():
    """Menyimpan state ke file"""
    state = {
        'active_bc_interval': {str(k): dict(v) for k, v in active_bc_interval.items()},
        'auto_replies': dict(auto_replies),
        'blacklist': list(blacklist),
        'active_groups': {str(k): dict(v) for k, v in active_groups.items()},
        'broadcast_data': {
            str(user_id): {
                bc_type: data for bc_type, data in user_data.items()
            } for user_id, user_data in broadcast_data.items()
        }
    }
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def load_state():
    """Memuat state dari file"""
    global active_bc_interval, auto_replies, blacklist, active_groups, broadcast_data
    
    if not os.path.exists(STATE_FILE):
        return
    
    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
        
        # Convert back to defaultdict
        active_bc_interval.clear()
        for user_id, data in state.get('active_bc_interval', {}).items():
            active_bc_interval[int(user_id)] = defaultdict(bool, data)
        
        auto_replies.clear()
        for user_id, reply in state.get('auto_replies', {}).items():
            auto_replies[int(user_id)] = reply
        
        blacklist.clear()
        blacklist.update(set(state.get('blacklist', [])))
        
        active_groups.clear()
        for group_id, data in state.get('active_groups', {}).items():
            active_groups[int(group_id)] = defaultdict(bool, data)
            
        broadcast_data.clear()
        for user_id, user_data in state.get('broadcast_data', {}).items():
            broadcast_data[int(user_id)] = user_data
            
    except Exception as e:
        print(f"Gagal memuat state: {e}")

async def run_broadcast(client, user_id, bc_type, message, interval):
    """Jalankan broadcast dalam background"""
    while active_bc_interval[user_id].get(bc_type, False):
        async for dialog in client.iter_dialogs():
            if dialog.is_group and dialog.id not in blacklist:
                try:
                    await client.send_message(dialog.id, message)
                except Exception:
                    pass
        await asyncio.sleep(interval)

async def restart_broadcasts(client, user_id):
    """Restart semua broadcast yang aktif untuk user tertentu"""
    for bc_type, is_active in active_bc_interval[user_id].items():
        if is_active and bc_type in broadcast_data.get(user_id, {}):
            data = broadcast_data[user_id][bc_type]
            asyncio.create_task(run_broadcast(client, user_id, bc_type, data['message'], data['interval']))

async def configure_event_handlers(client, user_id):
    """Konfigurasi semua fitur bot untuk user_id tertentu."""
    
    # Restart broadcast yang aktif
    await restart_broadcasts(client, user_id)
    
    # Restart auto-reply jika ada
    if user_id in auto_replies and auto_replies[user_id]:
        print(f"Auto-reply untuk user {user_id} diaktifkan kembali")
    
    @client.on(events.NewMessage(pattern=r'^crm hastle (.+) (\d+[smhd])$'))
    async def hastle_handler(event):
        custom_message, interval_str = event.pattern_match.groups()
        group_id = event.chat_id
        interval = parse_interval(interval_str)

        if not interval:
            await event.reply("⚠️ Format waktu salah! Gunakan format 10s, 1m, 2h, dll.")
            return

        if active_groups[group_id][user_id]:
            await event.reply("⚠️ Spam sudah berjalan untuk akun Anda di grup ini.")
            return

        active_groups[group_id][user_id] = True
        save_state()
        await event.reply(f"✅ Memulai spam: {custom_message} setiap {interval_str} untuk akun Anda.")
        while active_groups[group_id][user_id]:
            try:
                await client.send_message(group_id, custom_message)
                await asyncio.sleep(interval)
            except errors.FloodWaitError as e:
                await asyncio.sleep(e.seconds)
            except Exception:
                active_groups[group_id][user_id] = False
                save_state()

    @client.on(events.NewMessage(pattern=r'^crm stop$'))
    async def stop_handler(event):
        group_id = event.chat_id
        if active_groups[group_id][user_id]:
            active_groups[group_id][user_id] = False
            save_state()
            await event.reply("✅ Spam dihentikan untuk akun Anda di grup ini.")
        else:
            await event.reply("⚠️ Tidak ada spam yang berjalan untuk akun Anda di grup ini.")

    @client.on(events.NewMessage(pattern=r'^crm ping$'))
    async def ping_handler(event):
        await event.reply("🏓 Pong! Bot aktif.")

    @client.on(events.NewMessage(pattern=r'^crm bcstar (.+)$'))
    async def broadcast_handler(event):
        custom_message = event.pattern_match.group(1)
        await event.reply(f"✅ Memulai broadcast ke semua chat: {custom_message}")
        async for dialog in client.iter_dialogs():
            if dialog.id in blacklist:
                continue
            try:
                await client.send_message(dialog.id, custom_message)
            except Exception:
                pass

    @client.on(events.NewMessage(pattern=r'^crm bcstargr(\d+) (\d+[smhd])$'))
    async def broadcast_group_handler(event):
        # Split message into lines
        lines = event.raw_text.split('\n')
        if len(lines) < 2:
            await event.reply("⚠️ Format salah! Gunakan format:\ncrm bcstargrX [interval]\n[pesan multi-baris]")
            return
        
        group_number = event.pattern_match.group(1)
        interval_str = event.pattern_match.group(2)
        custom_message = '\n'.join(lines[1:])  # Ambil semua baris setelah baris pertama
        
        interval = parse_interval(interval_str)

        if not interval:
            await event.reply("⚠️ Format waktu salah! Gunakan format 10s, 1m, 2h, dll.")
            return

        bc_type = f"group{group_number}"
        if active_bc_interval[user_id][bc_type]:
            await event.reply(f"⚠️ Broadcast ke grup {group_number} sudah berjalan.")
            return

        # Simpan data broadcast
        broadcast_data[user_id][bc_type] = {
            'message': custom_message,
            'interval': interval
        }
        
        active_bc_interval[user_id][bc_type] = True
        save_state()
        
        await event.reply(f"✅ Memulai broadcast ke grup {group_number} dengan interval {interval_str}:\n{custom_message}")
        await run_broadcast(client, user_id, bc_type, custom_message, interval)

    @client.on(events.NewMessage(pattern=r'^crm stopbcstargr(\d+)$'))
    async def stop_broadcast_group_handler(event):
        group_number = event.pattern_match.group(1)
        bc_type = f"group{group_number}"
        if active_bc_interval[user_id][bc_type]:
            active_bc_interval[user_id][bc_type] = False
            save_state()
            await event.reply(f"✅ Broadcast ke grup {group_number} dihentikan.")
        else:
            await event.reply(f"⚠️ Tidak ada broadcast grup {group_number} yang berjalan.")

    @client.on(events.NewMessage(pattern=r'^crm bl$'))
    async def blacklist_handler(event):
        chat_id = event.chat_id
        blacklist.add(chat_id)
        save_state()
        await event.reply("✅ Grup ini telah ditambahkan ke blacklist.")

    @client.on(events.NewMessage(pattern=r'^crm unbl$'))
    async def unblacklist_handler(event):
        chat_id = event.chat_id
        if chat_id in blacklist:
            blacklist.remove(chat_id)
            save_state()
            await event.reply("✅ Grup ini telah dihapus dari blacklist.")
        else:
            await event.reply("⚠️ Grup ini tidak ada dalam blacklist.")

    @client.on(events.NewMessage(pattern=r'^crm help$'))
    async def help_handler(event):
        help_text = (
            "📋 **Daftar Perintah yang Tersedia:**\n\n"
            "1. crm hastle [pesan] [waktu][s/m/h/d]\n"
            "   Spam pesan di grup dengan interval tertentu.\n"
            "2. crm stop\n"
            "   Hentikan spam di grup.\n"
            "3. crm ping\n"
            "   Tes koneksi bot.\n"
            "4. crm bcstar [pesan]\n"
            "   Broadcast ke semua chat kecuali blacklist.\n"
            "5. crm bcstargr [waktu][s/m/h/d] [pesan]\n"
            "   Broadcast hanya ke grup dengan interval tertentu.\n"
            "6. crm stopbcstargr[1-10]\n"
            "   Hentikan broadcast ke grup tertentu.\n"
            "7. crm bl\n"
            "    Tambahkan grup/chat ke blacklist.\n"
            "8. crm unbl\n"
            "    Hapus grup/chat dari blacklist.\n"
        )
        await event.reply(help_text)

    @client.on(events.NewMessage(pattern=r'^crm setreply'))
    async def set_auto_reply(event):
        me = await client.get_me()
        uid = me.id
        message_lines = event.raw_text.split('\n', 1)
        if len(message_lines) < 2:
            await event.reply("⚠️ Harap isi auto-reply setelah baris pertama.\nContoh:\ncrm setreply\nHalo ini balasan otomatis.")
            return

        reply_message = message_lines[1]
        auto_replies[uid] = reply_message
        save_state()
        await event.reply("✅ Auto-reply berhasil diatur.")

    @client.on(events.NewMessage(incoming=True))
    async def auto_reply_handler(event):
        if event.is_private:
            me = await client.get_me()
            uid = me.id
            if uid in auto_replies and auto_replies[uid]:
                try:
                    sender = await event.get_sender()
                    peer = InputPeerUser(sender.id, sender.access_hash)
                    await client.send_message(peer, auto_replies[uid])
                    await client.send_read_acknowledge(peer)
                except errors.rpcerrorlist.UsernameNotOccupiedError:
                    pass
                except errors.rpcerrorlist.FloodWaitError:
                    pass
                except Exception:
                    pass

    @client.on(events.NewMessage(pattern=r'^crm stopall$'))
    async def stop_all_handler(event):
        for group_key in active_bc_interval[user_id].keys():
            active_bc_interval[user_id][group_key] = False
        auto_replies[user_id] = ""
        blacklist.clear()
        for group_id in active_groups.keys():
            active_groups[group_id][user_id] = False
        for group_key in active_bc_interval[user_id].keys():
            if active_bc_interval[user_id][group_key]:
                active_bc_interval[user_id][group_key] = False
        save_state()
        await event.reply("✅ Semua pengaturan telah direset dan semua broadcast dihentikan.")

# Load state saat module diimport
load_state()