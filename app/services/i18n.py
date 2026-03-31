"""
Простая система локализации для бота.
Язык хранится в bot_settings['bot_language'] (ru/en).
Строки редактируются через панель управления.
"""
from typing import Optional

# ── Строки по умолчанию ───────────────────────────────────────────────────────
STRINGS: dict[str, dict[str, str]] = {
    "ru": {
        # Главное меню
        "welcome": "👋 Привет, {name}!\n\nЭто VPN-бот. Выбери действие:",
        "welcome_back": "С возвращением, {name}!",
        "main_menu": "Главное меню:",

        # Кнопки меню
        "btn_my_keys": "🔑 Мои подписки",
        "btn_buy": "💳 Купить подписку",
        "btn_support": "💬 Поддержка",
        "btn_balance": "💰 Баланс",
        "btn_promo": "🎁 Промокод",

        # Покупка
        "choose_plan": "💳 <b>Выбери план подписки:</b>",
        "no_plans": "😔 Нет доступных тарифов. Попробуй позже.",
        "choose_payment": "💳 <b>{plan_name}</b> — {price} ₽\n\nВыбери способ оплаты:",
        "pay_card": "💳 Банковская карта (ЮКасса)",
        "pay_stars": "⭐ Telegram Stars ({stars} ⭐)",
        "pay_crypto": "₿ Криптовалюта (CryptoBot)",
        "pay_balance": "💰 Баланс ({balance:.0f} ₽)",
        "pay_back": "◀️ Назад",

        # Оплата
        "payment_success": "✅ Оплата прошла успешно!",
        "payment_pending": "⏳ Оплата ещё не поступила. Попробуйте позже.",
        "payment_failed": "❌ Платёж отменён.",
        "payment_error": "❌ Ошибка при создании платежа. Попробуй позже.",
        "payment_check": "🔄 Проверить оплату",
        "payment_go": "💳 Перейти к оплате",
        "subscription_url": "🔑 <b>Ссылка подписки:</b>\n<code>{url}</code>\n\n📅 Действует <b>{days} дней</b>\n\n💡 Скопируй ссылку и вставь в VPN-клиент",
        "key_error": "⚠️ Не удалось создать ключ. Обратитесь в поддержку.",

        # Подписки
        "my_keys_title": "📦 <b>Мои подписки</b>",
        "no_keys": "📦 У тебя пока нет подписок.\n\nКупи подписку, чтобы получить VPN-доступ.",
        "active_count": "✅ Активных: <b>{count}</b>",
        "archive_count": "🗂 В архиве: <b>{count}</b>",
        "no_active": "😔 Нет активных подписок",
        "archive_btn": "🗂 Архив ({count})",
        "archive_title": "🗂 <b>Архив подписок</b> ({count}):",
        "archive_empty": "Архив пуст",
        "back_main": "◀️ Главное меню",
        "back": "◀️ Назад",

        # Статусы
        "status_active": "✅ Активна",
        "status_expired": "⏰ Истекла",
        "status_revoked": "❌ Отозвана",

        # Баланс
        "balance_title": "💰 <b>Ваш баланс:</b> <b>{balance:.2f} ₽</b>",
        "referrals_count": "👥 <b>Рефералов:</b> {count}",
        "referral_bonus": "🎁 <b>Бонус за реферала:</b> {bonus}",
        "referral_link": "🔗 <b>Ваша реферальная ссылка:</b>\n<code>{link}</code>",

        # Промокод
        "enter_promo": "🎁 Введите промокод:",
        "promo_balance": "✅ Промокод применён!\n\n💰 На баланс зачислено <b>{value} ₽</b>",
        "promo_days": "✅ Промокод применён!\n\n📅 Добавлено <b>{value} дней</b> к подписке",
        "promo_discount": "✅ Промокод применён!\n\n🏷 Скидка <b>{value}%</b> на следующую покупку",
        "promo_invalid": "❌ Промокод недействителен или уже использован",

        # Поддержка
        "support_title": "💬 <b>Поддержка</b>",
        "support_no_tickets": "У вас нет обращений. Создайте новый тикет.",
        "support_tickets": "Ваши обращения ({count}):\n\nВыберите тикет или создайте новый.",
        "new_ticket": "➕ Новый тикет",
        "ticket_subject": "💬 <b>Новое обращение</b>\n\nВведите тему обращения (кратко):",
        "ticket_message": "📝 Тема: <b>{subject}</b>\n\nТеперь опишите вашу проблему подробнее:",
        "ticket_created": "✅ <b>Тикет #{id} создан!</b>\n\nТема: <b>{subject}</b>\n\nМы ответим вам в ближайшее время.",
        "ticket_closed": "✅ <b>Тикет #{id} закрыт.</b>\n\nСпасибо за обращение!",
        "ticket_reply_sent": "✅ Ответ по тикету #{id} отправлен!\n\nМы ответим вам в ближайшее время.",
        "ticket_not_found": "❌ Тикет не найден.",
        "write_reply": "✏️ Написать ответ",
        "close_ticket": "🔒 Закрыть тикет",

        # Системные
        "banned": "🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку.",
        "bot_disabled": "🔧 Бот временно отключён. Попробуйте позже.",
        "subscribe_channel": "📢 <b>Для использования бота необходимо подписаться на {channel}.</b>\n\nПосле подписки нажмите «Я подписался».",
        "subscribe_btn": "📢 Подписаться",
        "subscribed_btn": "✅ Я подписался",
        "not_subscribed": "❌ Вы ещё не подписались на канал.",
        "too_fast": "⏳ Слишком много запросов. Подождите немного.",

        # Профиль
        "profile_title": "👤 <b>Мой профиль</b>",
        "profile_id": "🆔 ID: <code>{id}</code>",
        "profile_name": "📛 Имя: <b>{name}</b>",
        "profile_username": "🔗 Username: {username}",
        "profile_reg": "📅 Регистрация: {date}",
        "profile_balance": "💰 Баланс: <b>{balance:.2f} ₽</b>",
        "profile_spent": "💳 Потрачено: <b>{spent:.2f} ₽</b>",
        "profile_active": "🔑 Активных подписок: <b>{count}</b>",
        "profile_archive": "🗂 В архиве: <b>{count}</b>",
        "profile_referrals": "👥 Рефералов: <b>{count}</b>",
        "profile_ref_link": "🔗 Реф. ссылка:\n<code>{link}</code>",
        "profile_ref_code": "🎫 Реф. код: <code>{code}</code>",
        "profile_expiry_warn": "⚠️ Ближайшее истечение: <b>{date}</b> (через {days} дн.)",
        "profile_expiry": "📅 Ближайшее истечение: <b>{date}</b>",
    },

    "en": {
        # Main menu
        "welcome": "👋 Hello, {name}!\n\nThis is a VPN bot. Choose an action:",
        "welcome_back": "Welcome back, {name}!",
        "main_menu": "Main menu:",

        # Menu buttons
        "btn_my_keys": "🔑 My subscriptions",
        "btn_buy": "💳 Buy subscription",
        "btn_support": "💬 Support",
        "btn_balance": "💰 Balance",
        "btn_promo": "🎁 Promo code",

        # Purchase
        "choose_plan": "💳 <b>Choose a subscription plan:</b>",
        "no_plans": "😔 No plans available. Try again later.",
        "choose_payment": "💳 <b>{plan_name}</b> — {price} ₽\n\nChoose payment method:",
        "pay_card": "💳 Bank card (YooKassa)",
        "pay_stars": "⭐ Telegram Stars ({stars} ⭐)",
        "pay_crypto": "₿ Cryptocurrency (CryptoBot)",
        "pay_balance": "💰 Balance ({balance:.0f} ₽)",
        "pay_back": "◀️ Back",

        # Payment
        "payment_success": "✅ Payment successful!",
        "payment_pending": "⏳ Payment not received yet. Try again later.",
        "payment_failed": "❌ Payment cancelled.",
        "payment_error": "❌ Error creating payment. Try again later.",
        "payment_check": "🔄 Check payment",
        "payment_go": "💳 Go to payment",
        "subscription_url": "🔑 <b>Subscription link:</b>\n<code>{url}</code>\n\n📅 Valid for <b>{days} days</b>\n\n💡 Copy the link and paste it into your VPN client",
        "key_error": "⚠️ Failed to create key. Contact support.",

        # Subscriptions
        "my_keys_title": "📦 <b>My subscriptions</b>",
        "no_keys": "📦 You have no subscriptions yet.\n\nBuy a subscription to get VPN access.",
        "active_count": "✅ Active: <b>{count}</b>",
        "archive_count": "🗂 Archived: <b>{count}</b>",
        "no_active": "😔 No active subscriptions",
        "archive_btn": "🗂 Archive ({count})",
        "archive_title": "🗂 <b>Subscription archive</b> ({count}):",
        "archive_empty": "Archive is empty",
        "back_main": "◀️ Main menu",
        "back": "◀️ Back",

        # Statuses
        "status_active": "✅ Active",
        "status_expired": "⏰ Expired",
        "status_revoked": "❌ Revoked",

        # Balance
        "balance_title": "💰 <b>Your balance:</b> <b>{balance:.2f} ₽</b>",
        "referrals_count": "👥 <b>Referrals:</b> {count}",
        "referral_bonus": "🎁 <b>Referral bonus:</b> {bonus}",
        "referral_link": "🔗 <b>Your referral link:</b>\n<code>{link}</code>",

        # Promo
        "enter_promo": "🎁 Enter promo code:",
        "promo_balance": "✅ Promo applied!\n\n💰 <b>{value} ₽</b> added to balance",
        "promo_days": "✅ Promo applied!\n\n📅 <b>{value} days</b> added to subscription",
        "promo_discount": "✅ Promo applied!\n\n🏷 <b>{value}%</b> discount on next purchase",
        "promo_invalid": "❌ Invalid or already used promo code",

        # Support
        "support_title": "💬 <b>Support</b>",
        "support_no_tickets": "You have no tickets. Create a new one.",
        "support_tickets": "Your tickets ({count}):\n\nSelect a ticket or create a new one.",
        "new_ticket": "➕ New ticket",
        "ticket_subject": "💬 <b>New ticket</b>\n\nEnter the subject (briefly):",
        "ticket_message": "📝 Subject: <b>{subject}</b>\n\nNow describe your problem in detail:",
        "ticket_created": "✅ <b>Ticket #{id} created!</b>\n\nSubject: <b>{subject}</b>\n\nWe'll reply soon.",
        "ticket_closed": "✅ <b>Ticket #{id} closed.</b>\n\nThank you for contacting us!",
        "ticket_reply_sent": "✅ Reply to ticket #{id} sent!\n\nWe'll respond soon.",
        "ticket_not_found": "❌ Ticket not found.",
        "write_reply": "✏️ Write reply",
        "close_ticket": "🔒 Close ticket",

        # System
        "banned": "🚫 Your account is banned. Contact support.",
        "bot_disabled": "🔧 Bot is temporarily disabled. Try again later.",
        "subscribe_channel": "📢 <b>You must subscribe to {channel} to use the bot.</b>\n\nAfter subscribing, press 'I subscribed'.",
        "subscribe_btn": "📢 Subscribe",
        "subscribed_btn": "✅ I subscribed",
        "not_subscribed": "❌ You haven't subscribed to the channel yet.",
        "too_fast": "⏳ Too many requests. Please wait.",

        # Profile
        "profile_title": "👤 <b>My Profile</b>",
        "profile_id": "🆔 ID: <code>{id}</code>",
        "profile_name": "📛 Name: <b>{name}</b>",
        "profile_username": "🔗 Username: {username}",
        "profile_reg": "📅 Registered: {date}",
        "profile_balance": "💰 Balance: <b>{balance:.2f} ₽</b>",
        "profile_spent": "💳 Spent: <b>{spent:.2f} ₽</b>",
        "profile_active": "🔑 Active subscriptions: <b>{count}</b>",
        "profile_archive": "🗂 Archived: <b>{count}</b>",
        "profile_referrals": "👥 Referrals: <b>{count}</b>",
        "profile_ref_link": "🔗 Ref link:\n<code>{link}</code>",
        "profile_ref_code": "🎫 Ref code: <code>{code}</code>",
        "profile_expiry_warn": "⚠️ Nearest expiry: <b>{date}</b> (in {days} days)",
        "profile_expiry": "📅 Nearest expiry: <b>{date}</b>",
    },
}


def t(key: str, lang: str = "ru", **kwargs) -> str:
    """Translate a key to the given language with optional format args."""
    lang_strings = STRINGS.get(lang, STRINGS["ru"])
    template = lang_strings.get(key, STRINGS["ru"].get(key, key))
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, ValueError):
            return template
    return template


def get_lang(settings: dict) -> str:
    """Get language from bot_settings dict."""
    return settings.get("bot_language", "ru")
