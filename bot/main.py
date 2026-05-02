import os
import logging
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from automation import run_account_creation

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

ALLOWED_USERS_STR = os.environ.get("ALLOWED_USERS", "")
ALLOWED_USERS = (
    set(int(x.strip()) for x in ALLOWED_USERS_STR.split(",") if x.strip())
    if ALLOWED_USERS_STR else set()
)

# Conversation states
ASK_COUNT, ASK_FULL_NAME, ASK_STK, ASK_BANK = range(4)

BANKS = [
    "Vietcombank", "Vietinbank", "BIDV", "Agribank",
    "MB Bank", "Techcombank", "VPBank", "ACB",
    "Sacombank", "TPBank", "OCB", "SHB",
    "HDBank", "VIB", "MSB", "SeABank",
]


def is_allowed(update: Update) -> bool:
    if not ALLOWED_USERS:
        return True
    return update.effective_user.id in ALLOWED_USERS


def bank_keyboard():
    buttons = []
    row = []
    for i, bank in enumerate(BANKS):
        row.append(InlineKeyboardButton(bank, callback_data=f"bank:{bank}"))
        if (i + 1) % 3 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


# ── /start ─────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Xin chào! Bot tự động tạo tài khoản.\n\n"
        "📋 *Lệnh có sẵn:*\n"
        "• /taotaikhoan — Tạo 1 tài khoản mới\n"
        "• /taonhieu — Tạo nhiều tài khoản\n"
        "• /huy — Huỷ thao tác hiện tại\n"
        "• /help — Hướng dẫn",
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Hướng dẫn sử dụng*\n\n"
        "1️⃣ /taotaikhoan — Tạo 1 tài khoản\n"
        "2️⃣ /taonhieu — Tạo nhiều tài khoản liên tiếp\n\n"
        "Bot sẽ hỏi:\n"
        "• Họ và tên đầy đủ\n"
        "• Số tài khoản ngân hàng\n"
        "• Chọn ngân hàng từ danh sách\n\n"
        "⚠️ Mỗi tài khoản mất khoảng 30–45 giây.",
        parse_mode="Markdown",
    )


# ── Bắt đầu luồng tạo tài khoản ───────────────────────────────────
async def tao_tai_khoan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        await update.message.reply_text("⛔ Bạn không có quyền dùng bot này.")
        return ConversationHandler.END
    context.user_data["count"] = 1
    await update.message.reply_text("✍️ Nhập *Họ và Tên đầy đủ*:", parse_mode="Markdown")
    return ASK_FULL_NAME


async def tao_nhieu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        await update.message.reply_text("⛔ Bạn không có quyền dùng bot này.")
        return ConversationHandler.END
    await update.message.reply_text(
        "🔢 Muốn tạo bao nhiêu tài khoản? (nhập số từ 1–10)"
    )
    return ASK_COUNT


async def ask_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or not (1 <= int(text) <= 10):
        await update.message.reply_text("❌ Vui lòng nhập số từ 1 đến 10.")
        return ASK_COUNT
    context.user_data["count"] = int(text)
    await update.message.reply_text("✍️ Nhập *Họ và Tên đầy đủ*:", parse_mode="Markdown")
    return ASK_FULL_NAME


async def ask_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    full_name = update.message.text.strip()
    if len(full_name) < 3:
        await update.message.reply_text("❌ Tên quá ngắn. Vui lòng nhập lại:")
        return ASK_FULL_NAME
    context.user_data["full_name"] = full_name
    await update.message.reply_text("🏦 Nhập *Số tài khoản ngân hàng* (STK):", parse_mode="Markdown")
    return ASK_STK


async def ask_stk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stk = update.message.text.strip()
    if not stk.isdigit() or len(stk) < 6:
        await update.message.reply_text("❌ STK không hợp lệ. Chỉ nhập số (tối thiểu 6 chữ số):")
        return ASK_STK
    context.user_data["stk"] = stk
    await update.message.reply_text(
        "🏛️ Chọn *Ngân hàng* của bạn:",
        parse_mode="Markdown",
        reply_markup=bank_keyboard(),
    )
    return ASK_BANK


async def ask_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    bank_name = query.data.replace("bank:", "")
    context.user_data["bank_name"] = bank_name

    full_name = context.user_data["full_name"]
    stk = context.user_data["stk"]
    count = context.user_data.get("count", 1)
    chat_id = update.effective_chat.id

    await query.edit_message_text(
        f"✅ Đã chọn: *{bank_name}*\n\n"
        f"👤 Họ tên: {full_name}\n"
        f"💳 STK: {stk}\n"
        f"🔢 Số tài khoản cần tạo: {count}\n\n"
        f"⏳ Bắt đầu xử lý...",
        parse_mode="Markdown",
    )

    def run_batch():
        from telegram import Bot
        bot = Bot(token=TELEGRAM_BOT_TOKEN)

        def send(text, markdown=False):
            try:
                bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="Markdown" if markdown else None,
                )
            except Exception:
                pass

        def progress(msg):
            send(msg)

        for i in range(1, count + 1):
            label = f" ({i}/{count})" if count > 1 else ""

            if count > 1:
                send(f"━━━━━━━━━━━━━━━━\n🤖 *Tài khoản {i}/{count}*", markdown=True)

            def on_creds(user, phone, password):
                send(
                    f"🆕 *Thông tin tài khoản{label}*\n\n"
                    f"👤 Tài khoản: `{user}`\n"
                    f"📱 SĐT: `+84{phone}`\n"
                    f"🔑 Mật khẩu: `{password}`\n"
                    f"👨‍💼 Họ tên: `{full_name.upper()}`\n"
                    f"🏦 Ngân hàng: {bank_name}\n"
                    f"💳 STK: `{stk}`\n\n"
                    f"⏳ Đang chạy tự động...",
                    markdown=True,
                )

            result = run_account_creation(
                full_name=full_name,
                stk=stk,
                bank_name=bank_name,
                progress_callback=progress,
                on_credentials_ready=on_creds,
            )

            if result["success"]:
                send(
                    f"✅ *Hoàn tất{label}!*\n\n"
                    + "\n".join(result["steps"]),
                    markdown=True,
                )
            else:
                send(
                    f"❌ *Thất bại{label}!*\n\n"
                    f"Lỗi: `{result.get('error', 'Không xác định')}`\n\n"
                    + "\n".join(result["steps"]),
                    markdown=True,
                )

        if count > 1:
            send(f"🎉 *Hoàn tất tất cả {count} tài khoản!*", markdown=True)

    thread = threading.Thread(target=run_batch, daemon=True)
    thread.start()

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🚫 Đã huỷ thao tác.")
    return ConversationHandler.END


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN chưa được cấu hình!")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("taotaikhoan", tao_tai_khoan),
            CommandHandler("taonhieu", tao_nhieu),
        ],
        states={
            ASK_COUNT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_count)],
            ASK_FULL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_full_name)],
            ASK_STK:       [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_stk)],
            ASK_BANK:      [CallbackQueryHandler(ask_bank, pattern="^bank:")],
        },
        fallbacks=[CommandHandler("huy", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(conv)

    logger.info("Bot đang chạy...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
