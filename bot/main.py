import os
import logging
import threading
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
from automation import run_account_creation

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# Tập hợp chat_id được phép dùng bot (để trống = cho phép tất cả)
ALLOWED_USERS_STR = os.environ.get("ALLOWED_USERS", "")
ALLOWED_USERS = (
    set(int(x.strip()) for x in ALLOWED_USERS_STR.split(",") if x.strip())
    if ALLOWED_USERS_STR
    else set()
)


def is_allowed(update: Update) -> bool:
    if not ALLOWED_USERS:
        return True
    return update.effective_user.id in ALLOWED_USERS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Xin chào! Đây là bot tự động tạo tài khoản.\n\n"
        "📋 *Lệnh có sẵn:*\n"
        "• /taotaikhoan — Tạo 1 tài khoản mới\n"
        "• /taonhieu <số> — Tạo nhiều tài khoản (tối đa 10)\n"
        "• /help — Hướng dẫn sử dụng",
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Hướng dẫn sử dụng*\n\n"
        "1️⃣ /taotaikhoan\n"
        "   → Tự động tạo 1 tài khoản ngẫu nhiên,\n"
        "     cài PIN bảo mật và thêm ngân hàng.\n\n"
        "2️⃣ /taonhieu <số>\n"
        "   → Tạo nhiều tài khoản liên tiếp.\n"
        "   Ví dụ: /taonhieu 3\n\n"
        "⚠️ Mỗi lần tạo mất khoảng 30-45 giây.",
        parse_mode="Markdown",
    )


def _run_and_notify(chat_id: int, token: str, index: int = 1, total: int = 1):
    bot = Bot(token=token)
    messages = []

    def progress(msg):
        try:
            bot.send_message(chat_id=chat_id, text=msg)
        except Exception:
            pass

    header = f"🤖 *Tạo tài khoản {index}/{total}*" if total > 1 else "🤖 *Bắt đầu tạo tài khoản...*"
    bot.send_message(chat_id=chat_id, text=header, parse_mode="Markdown")

    result = run_account_creation(progress_callback=progress)

    if result["success"]:
        summary = (
            f"✅ *Tài khoản {index}/{total} thành công!*\n\n"
            f"👤 Tài khoản: `{result['username']}`\n"
            f"📱 SĐT: `{result['phone']}`\n"
            f"🔑 Mật khẩu: `{result['password']}`\n\n"
            + "\n".join(result["steps"])
        )
    else:
        summary = (
            f"❌ *Tài khoản {index}/{total} thất bại!*\n\n"
            f"Lỗi: {result.get('error', 'Không xác định')}\n\n"
            + "\n".join(result["steps"])
        )

    bot.send_message(chat_id=chat_id, text=summary, parse_mode="Markdown")


async def tao_tai_khoan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        await update.message.reply_text("⛔ Bạn không có quyền dùng bot này.")
        return

    await update.message.reply_text("⏳ Đang bắt đầu tạo tài khoản, vui lòng chờ...")

    thread = threading.Thread(
        target=_run_and_notify,
        args=(update.effective_chat.id, TELEGRAM_BOT_TOKEN, 1, 1),
        daemon=True,
    )
    thread.start()


async def tao_nhieu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        await update.message.reply_text("⛔ Bạn không có quyền dùng bot này.")
        return

    try:
        count = int(context.args[0]) if context.args else 1
        count = max(1, min(count, 10))
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Sử dụng: /taonhieu <số> (tối đa 10)\nVí dụ: /taonhieu 3")
        return

    await update.message.reply_text(
        f"⏳ Sẽ tạo {count} tài khoản liên tiếp. Mỗi tài khoản mất ~30-45 giây..."
    )

    def run_batch():
        for i in range(1, count + 1):
            _run_and_notify(update.effective_chat.id, TELEGRAM_BOT_TOKEN, i, count)

    thread = threading.Thread(target=run_batch, daemon=True)
    thread.start()


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN chưa được cấu hình!")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("taotaikhoan", tao_tai_khoan))
    app.add_handler(CommandHandler("taonhieu", tao_nhieu))

    logger.info("Bot đang chạy...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
