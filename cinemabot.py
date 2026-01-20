import os
import aiosqlite
from aiohttp import ClientSession
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# подгрузка секретов
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
KINOPOISK_API_KEY = os.environ["KINOPOISK_API_KEY"]
DB_PATH = "cinema_bot.db"
KINOPOISK_SEARCH_URL = (
    "https://kinopoiskapiunofficial.tech/api/v2.1/films/search-by-keyword"
)


async def init_db() -> None:
    """
    Если нет таблиц searches и stats, создаёт их.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                query TEXT NOT NULL,
                ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS stats (
                user_id INTEGER NOT NULL,
                query TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, query)
            );
            """
        )
        await db.commit()


async def on_startup() -> None:
    await init_db()
    dp["session"] = ClientSession()


async def on_shutdown() -> None:
    await dp["session"].close()


if not BOT_TOKEN or not KINOPOISK_API_KEY:
    raise RuntimeError(
        "Нужно задать BOT_TOKEN и KINOPOISK_API_KEY в переменных окружения"
    )

bot = Bot(token=os.environ["BOT_TOKEN"])
dp = Dispatcher()


@dp.message(Command(commands=["start", "help"]))
async def cmd_help(message: types.Message) -> None:
    await message.answer(
        "<b>🎬 Добро пожаловать в CinemaBot!</b>\n\n"
        "🔎 Просто напиши название фильма — я найду его для тебя!\n\n"
        "<b>Остальные команды:</b>\n"
        "<b>/help</b> — показать эту справку\n"
        "<b>/history</b> — показать историю поисков (последние 10 фильмов)\n"
        "<b>/stats</b> — показать самые популярные запросы\n"
        "<b>/clear</b> — очистить историю поиска\n\n"
        "Приятного просмотра! 🍿",
        parse_mode="HTML",
    )


@dp.message(Command("history"))
async def cmd_history(message: types.Message) -> None:
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT query, ts 
            FROM searches
            WHERE user_id = ?
            ORDER BY ts DESC
            LIMIT 10
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()

    if not rows:
        await message.answer("У вас ещё нет истории поисковых запросов.")
        return

    text = "\n".join(f"{texsts} — <i>{query}</i>" for query, texsts in rows)
    await message.answer(
        "<b>Ваши последние запросы:</b>\n" + text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message) -> None:
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT query, count 
            FROM stats
            WHERE user_id = ?
            ORDER BY count DESC
            LIMIT 5
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()

    if not rows:
        await message.answer("Нет статистики запросов для вас.")
        return

    text = "\n".join(f"<i>{query}</i>: {cnt} раз" for query, cnt in rows)
    await message.answer(
        "<b>Топ‑5 ваших запросов:</b>\n" + text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.message(Command("clear"))
async def cmd_clear(message: types.Message) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM searches WHERE user_id = ?", (message.from_user.id,)
        )
        await db.execute("DELETE FROM stats WHERE user_id = ?", (message.from_user.id,))
        await db.commit()
    await message.answer("История и статистика очищены.")


@dp.message()
async def cmd_search(message: types.Message) -> None:
    query = message.text.strip()
    # === 1. запрос списка фильмов ===
    headers = {"X-API-KEY": KINOPOISK_API_KEY, "Accept": "application/json"}
    params = {"keyword": query, "page": 1}

    async with ClientSession() as session:
        resp = await session.get(KINOPOISK_SEARCH_URL, headers=headers, params=params)
        if resp.status != 200:
            return await message.answer(
                "Сервис КиноПоиск недоступен, попробуйте позже."
            )
        data = await resp.json()

        films = data.get("films") or []
        if not films:
            return await message.answer("К сожалению, ничего не найдено по запросу.")

        # первый (самый релевантный) результат
        film = films[0]
        film_id = film["filmId"]

        # === 2. дополнительный запрос за описанием ===
        detail_url = f"https://kinopoiskapiunofficial.tech/api/v2.1/films/{film_id}"
        detail_resp = await session.get(detail_url, headers=headers)
        detail = await detail_resp.json()

    # === 3. сбор данных о фильме ===
    title = film.get("nameRu") or film.get("nameEn") or "—"
    rating = detail.get("ratingKinopoisk") or film.get("rating") or "—"
    description = film.get("description") or film.get("shortDescription") or "-"
    poster = film.get("posterUrlPreview")
    year = film.get("year") or detail.get("year") or "—"
    url_gg = f"https://www.ggpoisk.ru/film/{film_id}/"
    duration = "—"
    raw_length = film.get("filmLength") or detail.get("filmLength")
    if raw_length:
        duration = str(raw_length)
    countries = film.get("countries") or []
    countries_str = ", ".join(c.get("country") for c in countries) or "—"

    # === 4. сохраняем в БД: searches and stats ===
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO searches(user_id, query) VALUES (?, ?)",
            (message.from_user.id, query),
        )
        await db.execute(
            """
            INSERT INTO stats(user_id, query, count)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, query) DO UPDATE
                SET count = count + 1
            """,
            (message.from_user.id, query),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT COUNT(*) FROM searches WHERE user_id = ?", (message.from_user.id,)
        )
        (total,) = await cursor.fetchone()

    # === 5. отвечаем пользователю ===
    MAX_DESCRIPTION = 500
    if len(description) > MAX_DESCRIPTION:
        description = description[:MAX_DESCRIPTION] + "..."
    MAX_COUNTRIES = 100
    if len(countries_str) > MAX_COUNTRIES:
        countries_str = countries_str[:MAX_COUNTRIES]

    caption = (
        f"<b>🎬 Название:</b> {title} ({year}, {countries_str})\n"
        f"<b>⭐ Рейтинг:</b> {rating}\n"
        f"<b>Длительность:</b> {duration}\n\n"
        f"<b>Описание:</b>\n{description}\n\n"
        f'<a href="{url_gg}"> Ссылка на просмотр на ggпоиск.</a>'
    )
    MAX_CAPTION = 1024
    if len(caption) > MAX_CAPTION:
        caption = caption[: MAX_CAPTION - 3] + "..."

    if poster:
        await message.answer_photo(
            poster, caption=caption, parse_mode="HTML", disable_web_page_preview=True
        )
    else:
        await message.answer(caption, parse_mode="HTML", disable_web_page_preview=True)

    if total == 5:
        await message.answer("Получено достижение: 🎖 Киноман")


dp.startup.register(on_startup)
dp.shutdown.register(on_shutdown)

if __name__ == "__main__":
    dp.run_polling(bot, skip_updates=True)
