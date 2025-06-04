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
active_groups = defaultdict(lambda: defaultdict(lambda: defaultdict(bool)))  # {user_id: {group_id: status}}
active_bc_interval = defaultdict(lambda: defaultdict(bool))  # {user_id: {bc_type: status}}
broadcast_data = defaultdict(dict)  # {user_id: {bc_type: {'message': str, 'interval': int}}}
blacklist = defaultdict(set)  # {user_id: set_of_chat_ids}
auto_replies = defaultdict(str)  # {user_id: reply_message}

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
        'blacklist': {str(k): list(v) for k, v in blacklist.items()},
        'active_groups': {
            str(user_id): {
                str(group_id): status for group_id, status in groups.items()
            } for user_id, groups in active_groups.items()
        },
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
        for user_id, chats in state.get('blacklist', {}).items():
            blacklist[int(user_id)] = set(chats)
        
        active_groups.clear()
        for user_id, groups in state.get('active_groups', {}).items():
            active_groups[int(user_id)] = defaultdict(lambda: defaultdict(bool))
            for group_id, status in groups.items():
                active_groups[int(user_id)][int(group_id)] = status
            
        broadcast_data.clear()
        for user_id, user_data in state.get('broadcast_data', {}).items():
            broadcast_data[int(user_id)] = user_data
            
    except Exception as e:
        print(f"Gagal memuat state: {e}")

async def run_broadcast(client, user_id, bc_type, message, interval):
    """Jalankan broadcast dalam background"""
    while active_bc_interval[user_id].get(bc_type, False):
        async for dialog in client.iter_dialogs():
            if dialog.is_group and dialog.id not in blacklist[user_id]:
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
    
    @client.on(events.NewMessage(pattern=r'^ary hastle (.+) (\d+[smhd])$'))
    async def hastle_handler(event):
        me = await client.get_me()
        current_user_id = me.id
        
        custom_message, interval_str = event.pattern_match.groups()
        group_id = event.chat_id
        interval = parse_interval(interval_str)

        if not interval:
            await event.reply("⚠️ Format waktu salah! Gunakan format 10s, 1m, 2h, dll.")
            return

        if active_groups[current_user_id][group_id]:
            await event.reply("⚠️ Spam sudah berjalan untuk akun Anda di grup ini.")
            return

        active_groups[current_user_id][group_id] = True
        save_state()
        await event.reply(f"✅ Memulai spam: {custom_message} setiap {interval_str} untuk akun Anda.")
        while active_groups[current_user_id][group_id]:
            try:
                await client.send_message(group_id, custom_message)
                await asyncio.sleep(interval)
            except errors.FloodWaitError as e:
                await asyncio.sleep(e.seconds)
            except Exception:
                active_groups[current_user_id][group_id] = False
                save_state()

    @client.on(events.NewMessage(pattern=r'^ary stop$'))
    async def stop_handler(event):
        me = await client.get_me()
        current_user_id = me.id
        
        group_id = event.chat_id
        if active_groups[current_user_id][group_id]:
            active_groups[current_user_id][group_id] = False
            save_state()
            await event.reply("✅ Spam dihentikan untuk akun Anda di grup ini.")
        else:
            await event.reply("⚠️ Tidak ada spam yang berjalan untuk akun Anda di grup ini.")

    @client.on(events.NewMessage(pattern=r'^ary ping$'))
    async def ping_handler(event):
        await event.reply("🏓 Pong! Bot aktif.")

    @client.on(events.NewMessage(pattern=r'^ary bcstar (.+)$'))
    async def broadcast_handler(event):
        me = await client.get_me()
        current_user_id = me.id
        
        custom_message = event.pattern_match.group(1)
        await event.reply(f"✅ Memulai broadcast ke semua chat: {custom_message}")
        async for dialog in client.iter_dialogs():
            if dialog.id in blacklist[current_user_id]:
                continue
            try:
                await client.send_message(dialog.id, custom_message)
            except Exception:
                pass

    @client.on(events.NewMessage(pattern=r'^ary bcstargr(\d+) (\d+[smhd]) (.+)$'))
    async def broadcast_group_handler(event):
        me = await client.get_me()
        current_user_id = me.id
        
        group_number = event.pattern_match.group(1)
        interval_str, custom_message = event.pattern_match.groups()[1:]
        interval = parse_interval(interval_str)

        if not interval:
            await event.reply("⚠️ Format waktu salah! Gunakan format 10s, 1m, 2h, dll.")
            return

        bc_type = f"group{group_number}"
        
        # Cek apakah bc_type ini sudah digunakan oleh akun lain
        for uid, bc_data in active_bc_interval.items():
            if uid != current_user_id and bc_data.get(bc_type, False):
                await event.reply(f"⚠️ Broadcast grup {group_number} sudah digunakan oleh akun lain!")
                return

        if active_bc_interval[current_user_id][bc_type]:
            await event.reply(f"⚠️ Broadcast ke grup {group_number} sudah berjalan.")
            return

        # Simpan data broadcast
        broadcast_data[current_user_id][bc_type] = {
            'message': custom_message,
            'interval': interval
        }
        
        active_bc_interval[current_user_id][bc_type] = True
        save_state()
        
        await event.reply(f"✅ Memulai broadcast ke grup {group_number} dengan interval {interval_str}: {custom_message}")
        await run_broadcast(client, current_user_id, bc_type, custom_message, interval)

    @client.on(events.NewMessage(pattern=r'^ary stopbcstargr(\d+)$'))
    async def stop_broadcast_group_handler(event):
        me = await client.get_me()
        current_user_id = me.id
        
        group_number = event.pattern_match.group(1)
        bc_type = f"group{group_number}"
        if active_bc_interval[current_user_id][bc_type]:
            active_bc_interval[current_user_id][bc_type] = False
            save_state()
            await event.reply(f"✅ Broadcast ke grup {group_number} dihentikan.")
        else:
            await event.reply(f"⚠️ Tidak ada broadcast grup {group_number} yang berjalan.")

    @client.on(events.NewMessage(pattern=r'^ary bl$'))
    async def blacklist_handler(event):
        me = await client.get_me()
        current_user_id = me.id
        
        chat_id = event.chat_id
        blacklist[current_user_id].add(chat_id)
        save_state()
        await event.reply("✅ Grup ini telah ditambahkan ke blacklist.")

    @client.on(events.NewMessage(pattern=r'^ary unbl$'))
    async def unblacklist_handler(event):
        me = await client.get_me()
        current_user_id = me.id
        
        chat_id = event.chat_id
        if chat_id in blacklist[current_user_id]:
            blacklist[current_user_id].remove(chat_id)
            save_state()
            await event.reply("✅ Grup ini telah dihapus dari blacklist.")
        else:
            await event.reply("⚠️ Grup ini tidak ada dalam blacklist.")

    @client.on(events.NewMessage(pattern=r'^ary help$'))
    async def help_handler(event):
        help_text = (
            "📋 **Daftar Perintah yang Tersedia:**\n\n"
            "1. ary hastle [pesan] [waktu][s/m/h/d]\n"
            "   Spam pesan di grup dengan interval tertentu.\n"
            "2. ary stop\n"
            "   Hentikan spam di grup.\n"
            "3. ary ping\n"
            "   Tes koneksi bot.\n"
            "4. ary bcstar [pesan]\n"
            "   Broadcast ke semua chat kecuali blacklist.\n"
            "5. ary bcstargr[1-10] [waktu][s/m/h/d] [pesan]\n"
            "   Broadcast hanya ke grup dengan interval tertentu.\n"
            "6. ary stopbcstargr[1-10]\n"
            "   Hentikan broadcast ke grup tertentu.\n"
            "7. ary bl\n"
            "    Tambahkan grup/chat ke blacklist.\n"
            "8. ary unbl\n"
            "    Hapus grup/chat dari blacklist.\n"
        )
        await event.reply(help_text)

    @client.on(events.NewMessage(pattern=r'^andra setreply'))
    async def set_auto_reply(event):
        me = await client.get_me()
        current_user_id = me.id
        
        message_lines = event.raw_text.split('\n', 1)
        if len(message_lines) < 2:
            await event.reply("⚠️ Harap isi auto-reply setelah baris pertama.\nContoh:\nandra setreply\nHalo ini balasan otomatis.")
            return

        reply_message = message_lines[1]
        auto_replies[current_user_id] = reply_message
        save_state()
        await event.reply("✅ Auto-reply berhasil diatur.")

    @client.on(events.NewMessage(incoming=True))
    async def auto_reply_handler(event):
        if event.is_private:
            me = await client.get_me()
            current_user_id = me.id
            
            # Periksa apakah auto-reply aktif dan pesan tidak berasal dari bot sendiri
            if current_user_id in auto_replies and auto_replies[current_user_id] and not event.out:
                try:
                    sender = await event.get_sender()
                    peer = InputPeerUser(sender.id, sender.access_hash)
                    await client.send_message(peer, auto_replies[current_user_id])
                    await client.send_read_acknowledge(peer)
                except errors.rpcerrorlist.UsernameNotOccupiedError:
                    pass
                except errors.rpcerrorlist.FloodWaitError:
                    pass
                except Exception:
                    pass

    @client.on(events.NewMessage(pattern=r'^ary stopall$'))
    async def stop_all_handler(event):
        me = await client.get_me()
        current_user_id = me.id
        
        # Stop semua broadcast grup
        active_bc_interval[current_user_id].clear()
        
        # Hapus auto-reply
        auto_replies[current_user_id] = ""
        
        # Kosongkan blacklist
        blacklist[current_user_id].clear()
        
        # Stop semua spam grup
        for group_id in list(active_groups[current_user_id].keys()):
            active_groups[current_user_id][group_id] = False
        
        # Hapus data broadcast
        if current_user_id in broadcast_data:
            broadcast_data[current_user_id].clear()
        
        save_state()
        await event.reply("✅ SEMUA FITUR TELAH DIHENTIKAN DAN DIHAPUS:\n"
                        "- Semua broadcast dihentikan\n"
                        "- Auto-reply dinonaktifkan\n"
                        "- Blacklist dikosongkan\n"
                        "- Semua spam grup dihentikan")

# Load state saat module diimport
load_state()