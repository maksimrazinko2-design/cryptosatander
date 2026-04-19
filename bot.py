import logging
import json
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)

# ⚠️ Замени на свои данные:
BOT_TOKEN  = "8642279855:AAGdUO3No1HcMn4rsf-a-4el6WUvGJnmmXc"
WEBAPP_URL = "https://project-yzlw9.vercel.app"

# Простое хранилище рефералов в памяти (для теста)
# В продакшене заменить на БД
referrals = {}   # {referrer_id: [referee_id, ...]}
user_pts  = {}   # {user_id: points}

REF_BONUS = 50   # очков за реферала
REF_BONUS_MSG = True  # отправлять уведомление

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    # Проверяем реферальный параметр
    if args and args[0].startswith('ref'):
        try:
            referrer_id = int(args[0][3:])
            if referrer_id != user.id:
                # Начисляем бонус рефереру
                if referrer_id not in referrals:
                    referrals[referrer_id] = []
                if user.id not in referrals[referrer_id]:
                    referrals[referrer_id].append(user.id)
                    user_pts[referrer_id] = user_pts.get(referrer_id, 0) + REF_BONUS

                    # Уведомляем реферера
                    try:
                        await context.bot.send_message(
                            chat_id=referrer_id,
                            text=f"🎉 <b>Новый реферал!</b>\n\n"
                                 f"👤 {user.first_name} зарегистрировался по твоей ссылке!\n"
                                 f"💰 Тебе начислено <b>+{REF_BONUS} очков</b> в игре!\n\n"
                                 f"👥 Всего рефералов: {len(referrals[referrer_id])}",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logging.warning(f"Не удалось уведомить реферера {referrer_id}: {e}")
        except (ValueError, IndexError):
            pass

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            text="🪐 Играть в SATANDER",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]])

    ref_count = len(referrals.get(user.id, []))
    ref_text  = f"\n👥 Твоих рефералов: <b>{ref_count}</b>" if ref_count > 0 else ""

    await update.message.reply_html(
        f"👋 Привет, <b>{user.first_name}</b>!\n\n"
        f"🪐 <b>SATANDER</b> — тапай и зарабатывай токены!\n\n"
        f"💡 1000 очков = 1 SATANDER токен\n"
        f"⬆️ Улучшай навыки за очки и токены{ref_text}\n\n"
        f"Нажми кнопку и начинай 👇",
        reply_markup=kb
    )


async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка данных из Web App (реферал, обмен)"""
    try:
        data = json.loads(update.effective_message.web_app_data.data)
        action = data.get('action')
        user = update.effective_user

        if action == 'referral':
            referrer_id = int(data.get('referrer', 0))
            if referrer_id and referrer_id != user.id:
                if referrer_id not in referrals:
                    referrals[referrer_id] = []
                if user.id not in referrals[referrer_id]:
                    referrals[referrer_id].append(user.id)
                    user_pts[referrer_id] = user_pts.get(referrer_id, 0) + REF_BONUS

                    try:
                        await context.bot.send_message(
                            chat_id=referrer_id,
                            text=f"🎉 <b>Новый реферал!</b>\n\n"
                                 f"👤 {user.first_name} присоединился по твоей ссылке!\n"
                                 f"💰 <b>+{REF_BONUS} очков</b> начислено!\n\n"
                                 f"👥 Всего рефералов: {len(referrals[referrer_id])}",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logging.warning(f"Ошибка уведомления реферера: {e}")

        elif action == 'exchange':
            wallet = data.get('wallet', '')
            tokens = data.get('tokens', 0)
            await update.message.reply_html(
                f"📋 <b>Заявка на обмен получена</b>\n\n"
                f"🪐 Токенов: <b>{tokens} SATANDER</b>\n"
                f"💳 Кошелёк: <code>{wallet}</code>\n\n"
                f"⏳ Интеграция с блокчейном в разработке.\n"
                f"Твоя заявка сохранена и будет обработана после запуска!"
            )

    except Exception as e:
        logging.error(f"Ошибка обработки webapp data: {e}")


async def refs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /refs — показать своих рефералов"""
    user_id = update.effective_user.id
    count   = len(referrals.get(user_id, []))
    pts     = user_pts.get(user_id, 0)
    await update.message.reply_html(
        f"👥 <b>Твои рефералы</b>\n\n"
        f"Приглашено друзей: <b>{count}</b>\n"
        f"Заработано с рефералов: <b>{pts} очков</b>\n\n"
        f"За каждого друга: <b>+{REF_BONUS} очков</b> 🎁"
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("refs",  refs_command))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    app.run_polling()


if __name__ == "__main__":
    main()
