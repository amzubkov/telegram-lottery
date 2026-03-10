import asyncio
import logging
import os
import random

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv

import db

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Temporary storage for raffle creation wizard
creation_state: dict[int, dict] = {}

EMOJI_FREE = "🟢"
EMOJI_RESERVED = "⬜"
EMOJI_PAID = "✅"
EMOJI_WINNER = "🏆"


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ──────────────────── Helpers ────────────────────

def ticket_grid_keyboard(tickets, raffle_id: int, admin: bool = False, winners=None):
    """Build inline keyboard grid for tickets."""
    winner_numbers = {w["ticket_number"] for w in winners} if winners else set()
    buttons = []
    row = []
    for t in tickets:
        num = t["number"]
        status = t["status"]
        if num in winner_numbers:
            emoji = EMOJI_WINNER
        elif status == "paid":
            emoji = EMOJI_PAID
        elif status == "reserved":
            emoji = EMOJI_RESERVED
        else:
            emoji = EMOJI_FREE

        cb = f"adm_ticket:{raffle_id}:{num}" if admin else f"ticket:{raffle_id}:{num}"
        row.append(InlineKeyboardButton(text=f"{emoji}{num}", callback_data=cb))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def raffle_info_text(raffle, paid_count: int, reserved_count: int, total: int) -> str:
    return (
        f"🎰 <b>{raffle['prize']}</b>\n\n"
        f"💰 Цена билета: <b>{raffle['price']}₽</b>\n"
        f"🏆 Победителей: <b>{raffle['winners_count']}</b>\n"
        f"📝 Билетов: {paid_count}{EMOJI_PAID} + {reserved_count}{EMOJI_RESERVED} / {total}\n\n"
        f"💳 Оплата: <b>{raffle['payment_info']}</b>\n\n"
        f"{EMOJI_FREE} свободен · {EMOJI_RESERVED} забронирован · {EMOJI_PAID} оплачен"
    )


# ──────────────────── User commands ────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message):
    if not is_admin(message.from_user.id):
        # Обычным пользователям показываем только справку
        raffle = await db.get_active_raffle()
        if raffle:
            await message.answer(
                f"🎰 <b>{raffle['prize']}</b>\n\n"
                f"Выбирайте билет в чате где опубликован розыгрыш!\n"
                f"/my — ваши билеты",
                parse_mode="HTML",
            )
        else:
            await message.answer("Сейчас нет активных розыгрышей.")
        return

    # Админ публикует сетку билетов
    raffle = await db.get_active_raffle()
    if not raffle:
        await message.answer("Нет активного розыгрыша. /new — создать.")
        return

    tickets = await db.get_tickets(raffle["id"])
    paid = sum(1 for t in tickets if t["status"] == "paid")
    reserved = sum(1 for t in tickets if t["status"] == "reserved")

    kb = ticket_grid_keyboard(tickets, raffle["id"])
    text = raffle_info_text(raffle, paid, reserved, len(tickets))

    if raffle["photo_id"]:
        await message.answer_photo(
            photo=raffle["photo_id"],
            caption=text,
            reply_markup=kb,
            parse_mode="HTML",
        )
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(Command("my"))
async def cmd_my_tickets(message: Message):
    raffle = await db.get_active_raffle()
    if not raffle:
        await message.answer("Нет активного розыгрыша.")
        return
    tickets = await db.get_user_tickets(raffle["id"], message.from_user.id)
    if not tickets:
        await message.answer("У вас нет билетов в текущем розыгрыше.")
        return
    lines = []
    for t in tickets:
        status = EMOJI_PAID if t["status"] == "paid" else EMOJI_RESERVED
        lines.append(f"{status} Билет #{t['number']}")
    buttons = []
    for t in tickets:
        if t["status"] == "reserved":
            buttons.append([InlineKeyboardButton(
                text=f"❌ Отменить #{t['number']}",
                callback_data=f"cancel:{raffle['id']}:{t['number']}",
            )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    await message.answer("\n".join(lines), reply_markup=kb)


async def _refresh_user_grid(callback: CallbackQuery, raffle, raffle_id: int):
    tickets = await db.get_tickets(raffle_id)
    paid = sum(1 for t in tickets if t["status"] == "paid")
    reserved = sum(1 for t in tickets if t["status"] == "reserved")
    kb = ticket_grid_keyboard(tickets, raffle_id)
    text = raffle_info_text(raffle, paid, reserved, len(tickets))
    try:
        if callback.message.photo:
            await callback.message.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
        else:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        pass


# ──────────────────── User ticket selection ────────────────────

@router.callback_query(F.data.startswith("ticket:"))
async def on_ticket_click(callback: CallbackQuery):
    _, raffle_id_str, num_str = callback.data.split(":")
    raffle_id, num = int(raffle_id_str), int(num_str)

    raffle = await db.get_raffle(raffle_id)
    if not raffle or raffle["status"] != "active":
        await callback.answer("Розыгрыш не активен.", show_alert=True)
        return

    user = callback.from_user
    ok = await db.reserve_ticket(raffle_id, num, user.id, user.username or "", user.first_name or "")
    if not ok:
        await callback.answer("Этот билет уже занят!", show_alert=True)
        await _refresh_user_grid(callback, raffle, raffle_id)
        return

    await callback.answer(
        f"Билет #{num} забронирован!\nПереведите {raffle['price']}₽\n{raffle['payment_info']}",
        show_alert=True,
    )

    # Notify admins
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🔔 <b>{user.first_name}</b> (@{user.username}) забронировал билет <b>#{num}</b>\n"
                f"Розыгрыш: {raffle['prize']}\nОжидается оплата: {raffle['price']}₽",
                parse_mode="HTML",
            )
        except Exception:
            pass

    await _refresh_user_grid(callback, raffle, raffle_id)


@router.callback_query(F.data.startswith("cancel:"))
async def on_cancel_ticket(callback: CallbackQuery):
    _, raffle_id_str, num_str = callback.data.split(":")
    raffle_id, num = int(raffle_id_str), int(num_str)

    ok = await db.cancel_ticket(raffle_id, num, callback.from_user.id)
    if ok:
        await callback.answer(f"Билет #{num} отменён.")
    else:
        await callback.answer("Не удалось отменить билет.", show_alert=True)


# ──────────────────── Admin: Create raffle ────────────────────

@router.message(Command("new"))
async def cmd_new_raffle(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Только для админов.")
        return
    creation_state[message.from_user.id] = {"step": "prize"}
    await message.answer("🎰 <b>Создание розыгрыша</b>\n\nШаг 1/5: Введите название приза:", parse_mode="HTML")


@router.message(F.photo, F.from_user.id.in_(creation_state))
async def wizard_photo_handler(message: Message):
    uid = message.from_user.id
    state = creation_state.get(uid)
    if not state or state["step"] != "photo":
        return
    state["photo_id"] = message.photo[-1].file_id
    raffle_id = await db.create_raffle(
        state["prize"], state["count"], state["price"], state["winners"], state["payment"], state["photo_id"]
    )
    del creation_state[uid]
    await message.answer(
        f"✅ Розыгрыш создан!\n\n"
        f"🎰 {state['prize']}\n"
        f"📝 Билетов: {state['count']}\n"
        f"💰 Цена: {state['price']}₽\n"
        f"🏆 Победителей: {state['winners']}\n"
        f"💳 Оплата: {state['payment']}\n"
        f"🖼 Фото: да\n\n"
        f"ID: {raffle_id}",
        parse_mode="HTML",
    )


@router.message(F.text, F.from_user.id.in_(creation_state))
async def wizard_handler(message: Message):
    uid = message.from_user.id
    if uid not in creation_state:
        return
    state = creation_state[uid]
    step = state["step"]

    if step == "prize":
        state["prize"] = message.text
        state["step"] = "count"
        await message.answer("Шаг 2/5: Сколько билетов?")
    elif step == "count":
        if not message.text.isdigit() or int(message.text) < 1:
            await message.answer("Введите число > 0")
            return
        state["count"] = int(message.text)
        state["step"] = "price"
        await message.answer("Шаг 3/5: Цена одного билета (₽)?")
    elif step == "price":
        if not message.text.isdigit() or int(message.text) < 1:
            await message.answer("Введите число > 0")
            return
        state["price"] = int(message.text)
        state["step"] = "winners"
        await message.answer("Шаг 4/5: Количество победителей?")
    elif step == "winners":
        if not message.text.isdigit() or int(message.text) < 1:
            await message.answer("Введите число > 0")
            return
        if int(message.text) > state["count"]:
            await message.answer("Победителей не может быть больше билетов!")
            return
        state["winners"] = int(message.text)
        state["step"] = "payment"
        await message.answer("Шаг 5/5: Реквизиты для оплаты (номер карты / СБП и т.д.)?")
    elif step == "payment":
        state["payment"] = message.text
        state["step"] = "photo"
        await message.answer("Шаг 6/6: Отправьте фото приза (или /skip чтобы пропустить):")
    elif step == "photo":
        if message.text and message.text.strip() == "/skip":
            state["photo_id"] = None
        else:
            await message.answer("Отправьте фото или /skip чтобы пропустить.")
            return
        raffle_id = await db.create_raffle(
            state["prize"], state["count"], state["price"], state["winners"], state["payment"], state["photo_id"]
        )
        del creation_state[uid]
        await message.answer(
            f"✅ Розыгрыш создан!\n\n"
            f"🎰 {state['prize']}\n"
            f"📝 Билетов: {state['count']}\n"
            f"💰 Цена: {state['price']}₽\n"
            f"🏆 Победителей: {state['winners']}\n"
            f"💳 Оплата: {state['payment']}\n"
            f"🖼 Фото: {'да' if state['photo_id'] else 'нет'}\n\n"
            f"ID: {raffle_id}",
            parse_mode="HTML",
        )


# ──────────────────── Admin: Manage tickets ────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    raffle = await db.get_active_raffle()
    if not raffle:
        await message.answer("Нет активного розыгрыша. /new — создать.")
        return

    tickets = await db.get_tickets(raffle["id"])
    paid = sum(1 for t in tickets if t["status"] == "paid")
    reserved = sum(1 for t in tickets if t["status"] == "reserved")
    total_money = paid * raffle["price"]

    text = (
        f"🔧 <b>Админ-панель</b>\n\n"
        f"🎰 {raffle['prize']}\n"
        f"💰 Собрано: {total_money}₽ ({paid} оплачено из {raffle['ticket_count']})\n"
        f"⬜ Забронировано: {reserved}\n"
        f"🟢 Свободно: {raffle['ticket_count'] - paid - reserved}\n\n"
        f"Нажми на билет чтобы переключить статус оплаты:"
    )

    kb = ticket_grid_keyboard(tickets, raffle["id"], admin=True)
    # Add draw button
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🎲 ПРОВЕСТИ РОЗЫГРЫШ", callback_data=f"draw:{raffle['id']}"),
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_admin:{raffle['id']}"),
        InlineKeyboardButton(text="🚫 Закрыть", callback_data=f"close:{raffle['id']}"),
    ])

    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("refresh_admin:"))
async def on_refresh_admin(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    raffle_id = int(callback.data.split(":")[1])
    raffle = await db.get_raffle(raffle_id)
    if not raffle:
        await callback.answer("Розыгрыш не найден.")
        return

    tickets = await db.get_tickets(raffle_id)
    paid = sum(1 for t in tickets if t["status"] == "paid")
    reserved = sum(1 for t in tickets if t["status"] == "reserved")
    total_money = paid * raffle["price"]

    text = (
        f"🔧 <b>Админ-панель</b>\n\n"
        f"🎰 {raffle['prize']}\n"
        f"💰 Собрано: {total_money}₽ ({paid} оплачено из {raffle['ticket_count']})\n"
        f"⬜ Забронировано: {reserved}\n"
        f"🟢 Свободно: {raffle['ticket_count'] - paid - reserved}\n\n"
        f"Нажми на билет чтобы переключить статус оплаты:"
    )
    kb = ticket_grid_keyboard(tickets, raffle_id, admin=True)
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🎲 ПРОВЕСТИ РОЗЫГРЫШ", callback_data=f"draw:{raffle_id}"),
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_admin:{raffle_id}"),
        InlineKeyboardButton(text="🚫 Закрыть", callback_data=f"close:{raffle_id}"),
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("adm_ticket:"))
async def on_admin_ticket_click(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Только для админов.")
        return

    _, raffle_id_str, num_str = callback.data.split(":")
    raffle_id, num = int(raffle_id_str), int(num_str)

    tickets = await db.get_tickets(raffle_id)
    ticket = next((t for t in tickets if t["number"] == num), None)
    if not ticket:
        await callback.answer("Билет не найден.")
        return

    if ticket["status"] == "free":
        await callback.answer("Билет свободен, никто не бронировал.", show_alert=True)
        return

    if ticket["status"] == "reserved":
        # Mark as paid
        await db.mark_paid(raffle_id, num)
        name = ticket["first_name"] or "?"
        await callback.answer(f"✅ Билет #{num} ({name}) — ОПЛАЧЕН")
    elif ticket["status"] == "paid":
        # Unmark paid -> reserved
        await db.mark_unpaid(raffle_id, num)
        name = ticket["first_name"] or "?"
        await callback.answer(f"⬜ Билет #{num} ({name}) — снята оплата")

    # Refresh admin panel
    raffle = await db.get_raffle(raffle_id)
    tickets = await db.get_tickets(raffle_id)
    paid = sum(1 for t in tickets if t["status"] == "paid")
    reserved = sum(1 for t in tickets if t["status"] == "reserved")
    total_money = paid * raffle["price"]

    text = (
        f"🔧 <b>Админ-панель</b>\n\n"
        f"🎰 {raffle['prize']}\n"
        f"💰 Собрано: {total_money}₽ ({paid} оплачено из {raffle['ticket_count']})\n"
        f"⬜ Забронировано: {reserved}\n"
        f"🟢 Свободно: {raffle['ticket_count'] - paid - reserved}\n\n"
        f"Нажми на билет чтобы переключить статус оплаты:"
    )
    kb = ticket_grid_keyboard(tickets, raffle_id, admin=True)
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🎲 ПРОВЕСТИ РОЗЫГРЫШ", callback_data=f"draw:{raffle_id}"),
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_admin:{raffle_id}"),
        InlineKeyboardButton(text="🚫 Закрыть", callback_data=f"close:{raffle_id}"),
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        pass


# ──────────────────── Admin: Draw winners ────────────────────

@router.callback_query(F.data.startswith("draw:"))
async def on_draw(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    raffle_id = int(callback.data.split(":")[1])
    raffle = await db.get_raffle(raffle_id)
    if not raffle or raffle["status"] != "active":
        await callback.answer("Розыгрыш не активен.")
        return

    tickets = await db.get_tickets(raffle_id)
    paid_tickets = [t for t in tickets if t["status"] == "paid"]

    if len(paid_tickets) < raffle["winners_count"]:
        await callback.answer(
            f"Недостаточно оплаченных билетов! Нужно минимум {raffle['winners_count']}, оплачено {len(paid_tickets)}.",
            show_alert=True,
        )
        return

    # Confirm
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, разыграть!", callback_data=f"draw_confirm:{raffle_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"refresh_admin:{raffle_id}"),
    ]])
    await callback.message.edit_text(
        f"🎲 <b>Провести розыгрыш?</b>\n\n"
        f"Оплаченных билетов: {len(paid_tickets)}\n"
        f"Победителей: {raffle['winners_count']}\n\n"
        f"Это действие необратимо!",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("draw_confirm:"))
async def on_draw_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    raffle_id = int(callback.data.split(":")[1])
    raffle = await db.get_raffle(raffle_id)
    if not raffle or raffle["status"] != "active":
        await callback.answer("Розыгрыш уже завершён.")
        return

    tickets = await db.get_tickets(raffle_id)
    paid_tickets = [t for t in tickets if t["status"] == "paid"]
    winner_tickets = random.sample(paid_tickets, raffle["winners_count"])
    winners = [(t["number"], t["user_id"]) for t in winner_tickets]

    await callback.answer()

    # ─── Анимация розыгрыша ───
    paid_numbers = [t["number"] for t in paid_tickets]
    msg = callback.message
    for i in range(8):
        # Показываем случайные номера, будто "крутим барабан"
        fake = random.sample(paid_numbers, min(raffle["winners_count"], len(paid_numbers)))
        spin_text = "🎰 <b>РОЗЫГРЫШ...</b>\n\n"
        dots = "." * ((i % 3) + 1)
        spin_text += f"🎲 Крутим барабан{dots}\n\n"
        for n in fake:
            spin_text += f"  ❓ #{n}\n"
        try:
            await msg.edit_text(spin_text, parse_mode="HTML")
        except Exception:
            pass
        await asyncio.sleep(0.7)

    # Финальный "замедляющийся" спин
    for i in range(3):
        fake = random.sample(paid_numbers, min(raffle["winners_count"], len(paid_numbers)))
        spin_text = "🎰 <b>РОЗЫГРЫШ...</b>\n\n"
        spin_text += "🥁 Почти готово...\n\n"
        for n in fake:
            spin_text += f"  ❓ #{n}\n"
        try:
            await msg.edit_text(spin_text, parse_mode="HTML")
        except Exception:
            pass
        await asyncio.sleep(1.2)

    # ─── Сохраняем и показываем результат ───
    await db.save_winners(raffle_id, winners)

    lines = [f"🎉🎉🎉 <b>РЕЗУЛЬТАТЫ РОЗЫГРЫША</b> 🎉🎉🎉\n\n🎰 {raffle['prize']}\n\n🏆 Победители:\n"]
    for t in winner_tickets:
        name = t["first_name"] or "?"
        uname = f" (@{t['username']})" if t["username"] else ""
        lines.append(f"  🏆 #{t['number']} — <b>{name}</b>{uname}")

    result_text = "\n".join(lines)

    all_winners = await db.get_winners(raffle_id)
    kb = ticket_grid_keyboard(tickets, raffle_id, winners=all_winners)
    await msg.edit_text(result_text + "\n\nПоздравляем! 🥳", reply_markup=kb, parse_mode="HTML")

    # Notify winners
    for t in winner_tickets:
        try:
            await bot.send_message(
                t["user_id"],
                f"🎉🎉🎉\n\n<b>Поздравляем! Вы выиграли в розыгрыше!</b>\n\n"
                f"🎰 {raffle['prize']}\n"
                f"🎫 Ваш билет: #{t['number']}\n\n"
                f"Свяжитесь с организатором для получения приза!",
                parse_mode="HTML",
            )
        except Exception:
            pass


# ──────────────────── Admin: Close raffle ────────────────────

@router.callback_query(F.data.startswith("close:"))
async def on_close(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    raffle_id = int(callback.data.split(":")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, закрыть", callback_data=f"close_confirm:{raffle_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"refresh_admin:{raffle_id}"),
    ]])
    await callback.message.edit_text("Закрыть розыгрыш без проведения?", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("close_confirm:"))
async def on_close_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    raffle_id = int(callback.data.split(":")[1])
    await db.close_raffle(raffle_id)
    await callback.message.edit_text("🚫 Розыгрыш закрыт.")
    await callback.answer()


# ──────────────────── Admin: List details ────────────────────

@router.message(Command("list"))
async def cmd_list(message: Message):
    if not is_admin(message.from_user.id):
        return
    raffle = await db.get_active_raffle()
    if not raffle:
        await message.answer("Нет активного розыгрыша.")
        return
    tickets = await db.get_tickets(raffle["id"])
    taken = [t for t in tickets if t["status"] in ("reserved", "paid")]
    if not taken:
        await message.answer("Пока никто не взял билеты.")
        return
    lines = [f"📋 <b>Список билетов — {raffle['prize']}</b>\n"]
    for t in taken:
        status = EMOJI_PAID if t["status"] == "paid" else EMOJI_RESERVED
        name = t["first_name"] or "?"
        uname = f" (@{t['username']})" if t["username"] else ""
        lines.append(f"{status} #{t['number']} — {name}{uname}")
    await message.answer("\n".join(lines), parse_mode="HTML")


# ──────────────────── Help ────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "🎰 <b>Бот розыгрышей</b>\n\n"
        "<b>Для всех:</b>\n"
        "/start — текущий розыгрыш\n"
        "/my — мои билеты\n\n"
    )
    if is_admin(message.from_user.id):
        text += (
            "<b>Для админов:</b>\n"
            "/new — создать розыгрыш\n"
            "/admin — панель управления\n"
            "/list — список участников\n"
        )
    await message.answer(text, parse_mode="HTML")


# ──────────────────── Main ────────────────────

async def main():
    await db.init_db()
    logger.info("Bot starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
