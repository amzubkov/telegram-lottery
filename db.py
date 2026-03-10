import aiosqlite
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "lottery.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS raffles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prize TEXT NOT NULL,
                ticket_count INTEGER NOT NULL,
                price INTEGER NOT NULL,
                winners_count INTEGER NOT NULL,
                payment_info TEXT NOT NULL DEFAULT '',
                photo_id TEXT,
                chat_id INTEGER,
                message_id INTEGER,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raffle_id INTEGER NOT NULL,
                number INTEGER NOT NULL,
                user_id INTEGER,
                username TEXT,
                first_name TEXT,
                status TEXT NOT NULL DEFAULT 'free',
                reserved_at TIMESTAMP,
                paid_at TIMESTAMP,
                FOREIGN KEY (raffle_id) REFERENCES raffles(id),
                UNIQUE(raffle_id, number)
            );
            CREATE TABLE IF NOT EXISTS winners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raffle_id INTEGER NOT NULL,
                ticket_number INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                FOREIGN KEY (raffle_id) REFERENCES raffles(id)
            );
        """)
        await db.commit()


async def create_raffle(prize: str, ticket_count: int, price: int, winners_count: int, payment_info: str, photo_id: str = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO raffles (prize, ticket_count, price, winners_count, payment_info, photo_id) VALUES (?, ?, ?, ?, ?, ?)",
            (prize, ticket_count, price, winners_count, payment_info, photo_id),
        )
        raffle_id = cursor.lastrowid
        for i in range(1, ticket_count + 1):
            await db.execute(
                "INSERT INTO tickets (raffle_id, number, status) VALUES (?, ?, 'free')",
                (raffle_id, i),
            )
        await db.commit()
        return raffle_id


async def save_group_message(raffle_id: int, chat_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE raffles SET chat_id = ?, message_id = ? WHERE id = ?",
            (chat_id, message_id, raffle_id),
        )
        await db.commit()


async def get_active_raffle():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM raffles WHERE status = 'active' ORDER BY id DESC LIMIT 1")
        return await cursor.fetchone()


async def get_raffle(raffle_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM raffles WHERE id = ?", (raffle_id,))
        return await cursor.fetchone()


async def get_tickets(raffle_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM tickets WHERE raffle_id = ? ORDER BY number", (raffle_id,)
        )
        return await cursor.fetchall()


async def reserve_ticket(raffle_id: int, number: int, user_id: int, username: str, first_name: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT status FROM tickets WHERE raffle_id = ? AND number = ?",
            (raffle_id, number),
        )
        row = await cursor.fetchone()
        if not row or row[0] != "free":
            return False
        await db.execute(
            """UPDATE tickets SET status = 'reserved', user_id = ?, username = ?, first_name = ?,
               reserved_at = CURRENT_TIMESTAMP WHERE raffle_id = ? AND number = ?""",
            (user_id, username, first_name, raffle_id, number),
        )
        await db.commit()
        return True


async def cancel_ticket(raffle_id: int, number: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT status, user_id FROM tickets WHERE raffle_id = ? AND number = ?",
            (raffle_id, number),
        )
        row = await cursor.fetchone()
        if not row or row[0] != "reserved" or row[1] != user_id:
            return False
        await db.execute(
            "UPDATE tickets SET status = 'free', user_id = NULL, username = NULL, first_name = NULL, reserved_at = NULL WHERE raffle_id = ? AND number = ?",
            (raffle_id, number),
        )
        await db.commit()
        return True


async def mark_paid(raffle_id: int, number: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT status FROM tickets WHERE raffle_id = ? AND number = ?",
            (raffle_id, number),
        )
        row = await cursor.fetchone()
        if not row or row[0] != "reserved":
            return False
        await db.execute(
            "UPDATE tickets SET status = 'paid', paid_at = CURRENT_TIMESTAMP WHERE raffle_id = ? AND number = ?",
            (raffle_id, number),
        )
        await db.commit()
        return True


async def mark_unpaid(raffle_id: int, number: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT status FROM tickets WHERE raffle_id = ? AND number = ?",
            (raffle_id, number),
        )
        row = await cursor.fetchone()
        if not row or row[0] != "paid":
            return False
        await db.execute(
            "UPDATE tickets SET status = 'reserved', paid_at = NULL WHERE raffle_id = ? AND number = ?",
            (raffle_id, number),
        )
        await db.commit()
        return True


async def get_user_tickets(raffle_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM tickets WHERE raffle_id = ? AND user_id = ? ORDER BY number",
            (raffle_id, user_id),
        )
        return await cursor.fetchall()


async def save_winners(raffle_id: int, winners: list[tuple[int, int]]):
    async with aiosqlite.connect(DB_PATH) as db:
        for ticket_number, user_id in winners:
            await db.execute(
                "INSERT INTO winners (raffle_id, ticket_number, user_id) VALUES (?, ?, ?)",
                (raffle_id, ticket_number, user_id),
            )
        await db.execute("UPDATE raffles SET status = 'finished' WHERE id = ?", (raffle_id,))
        await db.commit()


async def get_winners(raffle_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT w.ticket_number, t.user_id, t.username, t.first_name
               FROM winners w JOIN tickets t ON w.raffle_id = t.raffle_id AND w.ticket_number = t.number
               WHERE w.raffle_id = ?""",
            (raffle_id,),
        )
        return await cursor.fetchall()


async def close_raffle(raffle_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE raffles SET status = 'closed' WHERE id = ?", (raffle_id,))
        await db.commit()
