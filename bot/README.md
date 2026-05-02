# Telegram Bot - Tự Động Tạo Tài Khoản

Bot Telegram tự động tạo tài khoản khách hàng, cài đặt mã PIN bảo mật và thêm thông tin ngân hàng trên website mục tiêu.

## Tính năng

- `/taotaikhoan` — Tạo 1 tài khoản mới tự động
- `/taonhieu <số>` — Tạo nhiều tài khoản liên tiếp (tối đa 10)
- Gửi cập nhật tiến trình trực tiếp qua Telegram
- Hỗ trợ giới hạn người dùng được phép dùng bot

## Quy trình tự động

1. **Đăng ký** — Điền form đăng ký với thông tin ngẫu nhiên
2. **Cài PIN** — Thiết lập mã PIN bảo mật rút tiền
3. **Ngân hàng** — Thêm tài khoản ngân hàng

## Deploy lên Railway

### Bước 1: Fork/Clone repo này

### Bước 2: Tạo project mới trên Railway
1. Vào [railway.app](https://railway.app)
2. Nhấn **New Project** → **Deploy from GitHub repo**
3. Chọn repo này, chọn thư mục `bot/`

### Bước 3: Cấu hình biến môi trường (Variables)
Thêm các biến sau trong Railway dashboard:

| Biến | Mô tả |
|------|-------|
| `TELEGRAM_BOT_TOKEN` | Token bot từ @BotFather |
| `BASE_URL` | URL website mục tiêu |
| `FULL_NAME` | Họ và tên đầy đủ |
| `STK` | Số tài khoản ngân hàng |
| `BANK_NAME` | Tên ngân hàng |
| `WITHDRAW_PASS` | Mã PIN bảo mật |
| `ALLOWED_USERS` | (Tùy chọn) Danh sách Telegram user ID, phân cách bởi dấu phẩy |

### Bước 4: Deploy
Railway sẽ tự động build và chạy bot.

## Chạy local

```bash
cd bot
pip install -r requirements.txt
cp .env.example .env
# Điền thông tin vào .env
python main.py
```

## Lưu ý

- Mỗi lần tạo tài khoản mất khoảng 30-45 giây
- Cần có Chrome/Chromium được cài đặt
- Railway tự động cài Chromium qua `nixpacks.toml`
