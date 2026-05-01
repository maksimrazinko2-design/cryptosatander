import logging
import json
import os
import asyncpg
from aiohttp import web
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)

BOT_TOKEN    = os.getenv("BOT_TOKEN", "")
WEBAPP_URL   = os.getenv("WEBAPP_URL", "https://project-yzlw9.vercel.app")
DATABASE_URL = os.getenv("DATABASE_URL", "")
PORT         = int(os.getenv("PORT", 8080))
REF_BONUS    = 50

CORS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
}

async def get_db():
    return await asyncpg.connect(DATABASE_URL)

async def init_db():
    conn = await get_db()
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     BIGINT PRIMARY KEY,
                username    TEXT DEFAULT '',
                first_name  TEXT DEFAULT '',
                ref_pts     INTEGER DEFAULT 0,
                refs_count  INTEGER DEFAULT 0,
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id          SERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                referee_id  BIGINT NOT NULL,
                created_at  TIMESTAMP DEFAULT NOW(),
                UNIQUE(referrer_id, referee_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_bonuses (
                id         SERIAL PRIMARY KEY,
                user_id    BIGINT NOT NULL,
                pts        INTEGER NOT NULL,
                reason     TEXT DEFAULT 'referral',
                claimed    BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS game_state (
                user_id        BIGINT PRIMARY KEY,
                pts            FLOAT DEFAULT 0,
                total          FLOAT DEFAULT 0,
                energy         FLOAT DEFAULT 100,
                e_ts           BIGINT DEFAULT 0,
                auto_ts        BIGINT DEFAULT 0,
                tap_power_lvl  INTEGER DEFAULT 0,
                autotap_lvl    INTEGER DEFAULT 0,
                multiplier_lvl INTEGER DEFAULT 0,
                energy_lvl     INTEGER DEFAULT 0,
                updated_at     TIMESTAMP DEFAULT NOW()
            )
        """)
        logging.info("DB initialized!")
    finally:
        await conn.close()

async def get_or_create_user(user_id, username='', first_name=''):
    conn = await get_db()
    try:
        await conn.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO UPDATE SET username=$2, first_name=$3
        """, user_id, username or '', first_name or 'Игрок')
    finally:
        await conn.close()

async def add_referral(referrer_id, referee_id):
    conn = await get_db()
    try:
        existing = await conn.fetchval("""
            SELECT id FROM referrals WHERE referrer_id=$1 AND referee_id=$2
        """, referrer_id, referee_id)
        if existing:
            return False
        await conn.execute("""
            INSERT INTO referrals (referrer_id, referee_id) VALUES ($1, $2)
        """, referrer_id, referee_id)
        await conn.execute("""
            UPDATE users SET ref_pts=ref_pts+$1, refs_count=refs_count+1 WHERE user_id=$2
        """, REF_BONUS, referrer_id)
        await conn.execute("""
            INSERT INTO pending_bonuses (user_id, pts) VALUES ($1, $2)
        """, referrer_id, REF_BONUS)
        logging.info(f"Referral added: referrer={referrer_id}, referee={referee_id}, bonus={REF_BONUS}")
        return True
    except Exception as e:
        logging.error(f"add_referral error: {e}")
        return False
    finally:
        await conn.close()

async def get_pending_bonuses(user_id):
    conn = await get_db()
    try:
        total = await conn.fetchval("""
            SELECT COALESCE(SUM(pts),0) FROM pending_bonuses
            WHERE user_id=$1 AND claimed=FALSE
        """, user_id)
        return int(total or 0)
    finally:
        await conn.close()

async def claim_bonuses(user_id):
    conn = await get_db()
    try:
        total = await conn.fetchval("""
            SELECT COALESCE(SUM(pts),0) FROM pending_bonuses
            WHERE user_id=$1 AND claimed=FALSE
        """, user_id)
        if total and total > 0:
            await conn.execute("""
                UPDATE pending_bonuses SET claimed=TRUE
                WHERE user_id=$1 AND claimed=FALSE
            """, user_id)
        return int(total or 0)
    finally:
        await conn.close()

async def get_user_stats(user_id):
    conn = await get_db()
    try:
        row = await conn.fetchrow(
            "SELECT ref_pts, refs_count FROM users WHERE user_id=$1", user_id
        )
        return dict(row) if row else {'ref_pts': 0, 'refs_count': 0}
    finally:
        await conn.close()

async def api_options(request):
    return web.Response(headers={**CORS, 'Allow': 'GET, POST, OPTIONS'})

async def api_claim(request):
    try:
        user_id = int(request.rel_url.query.get('user_id', 0))
        if not user_id:
            return web.json_response({'ok': False}, headers=CORS)
        pts   = await claim_bonuses(user_id)
        stats = await get_user_stats(user_id)
        return web.json_response({
            'ok': True, 'bonus_pts': pts,
            'refs_count': stats['refs_count'],
            'ref_pts_total': stats['ref_pts'],
        }, headers=CORS)
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, headers=CORS)

async def api_stats(request):
    try:
        user_id = int(request.rel_url.query.get('user_id', 0))
        if not user_id:
            return web.json_response({'ok': False}, headers=CORS)
        pending = await get_pending_bonuses(user_id)
        stats   = await get_user_stats(user_id)
        return web.json_response({
            'ok': True, 'pending_pts': pending,
            'refs_count': stats['refs_count'],
            'ref_pts_total': stats['ref_pts'],
        }, headers=CORS)
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, headers=CORS)

async def api_health(request):
    return web.json_response({'ok': True, 'status': 'alive'}, headers=CORS)

async def api_refs_list(request):
    try:
        user_id = int(request.rel_url.query.get('user_id', 0))
        if not user_id:
            return web.json_response({'ok': False}, headers=CORS)
        conn = await get_db()
        try:
            rows = await conn.fetch("""
                SELECT u.user_id, u.first_name, u.username, r.created_at
                FROM referrals r
                JOIN users u ON u.user_id = r.referee_id
                WHERE r.referrer_id = $1
                ORDER BY r.created_at DESC
            """, user_id)
            refs = []
            for row in rows:
                refs.append({
                    'user_id':    row['user_id'],
                    'first_name': row['first_name'] or 'Игрок',
                    'username':   row['username'] or '',
                    'joined':     row['created_at'].strftime('%d.%m.%Y'),
                })
            return web.json_response({'ok': True, 'refs': refs}, headers=CORS)
        finally:
            await conn.close()
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, headers=CORS)

async def api_save(request):
    try:
        user_id = int(request.rel_url.query.get('user_id', 0))
        if not user_id:
            return web.json_response({'ok': False}, headers=CORS)
        body = await request.json()
        conn = await get_db()
        try:
            await conn.execute("""
                INSERT INTO game_state (
                    user_id, pts, total, energy, e_ts, auto_ts,
                    tap_power_lvl, autotap_lvl, multiplier_lvl, energy_lvl, updated_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    pts=$2, total=$3, energy=$4, e_ts=$5, auto_ts=$6,
                    tap_power_lvl=$7, autotap_lvl=$8, multiplier_lvl=$9, energy_lvl=$10,
                    updated_at=NOW()
            """,
            user_id,
            float(body.get('pts', 0)),
            float(body.get('total', 0)),
            float(body.get('energy', 100)),
            int(body.get('eTs', 0)),
            int(body.get('autoTs', 0)),
            int(body.get('tap_power_lvl', 0)),
            int(body.get('autotap_lvl', 0)),
            int(body.get('multiplier_lvl', 0)),
            int(body.get('energy_lvl', 0)),
            )
            return web.json_response({'ok': True}, headers=CORS)
        finally:
            await conn.close()
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, headers=CORS)

async def api_load(request):
    try:
        user_id = int(request.rel_url.query.get('user_id', 0))
        if not user_id:
            return web.json_response({'ok': False}, headers=CORS)
        conn = await get_db()
        try:
            row = await conn.fetchrow(
                "SELECT * FROM game_state WHERE user_id=$1", user_id
            )
            if row:
                return web.json_response({'ok': True, 'state': dict(row)}, headers=CORS)
            return web.json_response({'ok': True, 'state': None}, headers=CORS)
        finally:
            await conn.close()
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, headers=CORS)

async def start_api_server():
    app_api = web.Application()
    app_api.router.add_get('/api/claim',     api_claim)
    app_api.router.add_get('/api/stats',     api_stats)
    app_api.router.add_get('/api/health',    api_health)
    app_api.router.add_get('/api/load',      api_load)
    app_api.router.add_get('/api/refs_list', api_refs_list)
    app_api.router.add_post('/api/save',     api_save)
    app_api.router.add_options('/{path:.*}', api_options)
    runner = web.AppRunner(app_api)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    logging.info(f"API running on port {PORT}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await get_or_create_user(user.id, user.username, user.first_name)
    if context.args and context.args[0].startswith('ref'):
        try:
            referrer_id = int(context.args[0][3:])
            if referrer_id != user.id:
                is_new = await add_referral(referrer_id, user.id)
                if is_new:
                    stats = await get_user_stats(referrer_id)
                    try:
                        await context.bot.send_message(
                            chat_id=referrer_id,
                            text=(
                                f"🎉 <b>Новый реферал!</b>\n\n"
                                f"👤 {user.first_name} присоединился по твоей ссылке!\n"
                                f"💰 <b>+{REF_BONUS} очков</b> ждут тебя в игре!\n\n"
                                f"👥 Всего рефералов: <b>{stats['refs_count']}</b>\n\n"
                                f"Открой игру чтобы получить очки 👇"
                            ),
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup([[
                                InlineKeyboardButton("🪐 Получить очки", web_app=WebAppInfo(url=WEBAPP_URL))
                            ]])
                        )
                    except Exception as e:
                        logging.warning(f"Ошибка уведомления: {e}")
        except (ValueError, IndexError):
            pass
    stats = await get_user_stats(user.id)
    ref_text = f"\n👥 Твоих рефералов: <b>{stats['refs_count']}</b>" if stats['refs_count'] > 0 else ""
    await update.message.reply_html(
        f"👋 Привет, <b>{user.first_name}</b>!\n\n"
        f"🪐 <b>SATANDER</b> — тапай и зарабатывай!\n\n"
        f"👆 Тапай кнопку — копи очки\n"
        f"⬆️ Улучшай навыки — тапай эффективнее\n"
        f"👥 Приглашай друзей — получай бонусы{ref_text}\n\n"
        f"Нажми кнопку и начинай 👇",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🪐 Играть в SATANDER", web_app=WebAppInfo(url=WEBAPP_URL))
        ]])
    )

async def refs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats   = await get_user_stats(user_id)
    pending = await get_pending_bonuses(user_id)
    pending_text = (
        f"\n⏳ Ожидает: <b>+{pending} очков</b> (открой игру!)" if pending > 0 else ""
    )
    await update.message.reply_html(
        f"👥 <b>Твои рефералы</b>\n\n"
        f"Приглашено: <b>{stats['refs_count']}</b>\n"
        f"Заработано: <b>{stats['ref_pts']} очков</b>{pending_text}\n\n"
        f"За каждого друга: <b>+{REF_BONUS} очков</b> 🎁"
    )

async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = json.loads(update.effective_message.web_app_data.data)
        user = update.effective_user
        await get_or_create_user(user.id, user.username, user.first_name)
        if data.get('action') == 'referral':
            referrer_id = int(data.get('referrer', 0))
            if referrer_id and referrer_id != user.id:
                is_new = await add_referral(referrer_id, user.id)
                if is_new:
                    stats = await get_user_stats(referrer_id)
                    try:
                        await context.bot.send_message(
                            chat_id=referrer_id,
                            text=(
                                f"🎉 <b>Новый реферал!</b>\n\n"
                                f"👤 {user.first_name} присоединился!\n"
                                f"💰 <b>+{REF_BONUS} очков</b> ждут в игре!\n"
                                f"👥 Рефералов: <b>{stats['refs_count']}</b>\n\n"
                                f"Открой игру чтобы получить очки 👇"
                            ),
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup([[
                                InlineKeyboardButton("🪐 Получить очки", web_app=WebAppInfo(url=WEBAPP_URL))
                            ]])
                        )
                    except Exception as e:
                        logging.warning(f"Ошибка: {e}")
    except Exception as e:
        logging.error(f"webapp_data error: {e}")

async def post_init(application):
    await init_db()
    await start_api_server()

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("refs",  refs_command))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    app.run_polling()

if __name__ == "__main__":
    main()
