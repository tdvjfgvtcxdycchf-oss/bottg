import logging
import os
import json
from datetime import date, datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import TelegramError
from telegram.request import HTTPXRequest
from dotenv import load_dotenv

# Загрузка переменных окружения из файла
load_dotenv('config.env')

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ID группы для пересылки сообщений (задается в .env файле)
TARGET_GROUP_ID = os.getenv('TARGET_GROUP_ID', '-1002904494745')

# Ограничение на количество сообщений в день
DAILY_MESSAGE_LIMIT = 10

# Файл для сохранения данных
DATA_FILE = 'bot_data.json'

class MessageForwarderBot:
    def __init__(self, token: str):
        self.token = token
        proxy_url = os.getenv('PROXY_URL')
        builder = Application.builder().token(token)
        if proxy_url:
            builder = builder.request(HTTPXRequest(proxy=proxy_url))
        self.application = builder.build()
        self.setup_handlers()
    
    def setup_handlers(self):
        """Настройка обработчиков команд и сообщений"""
        # Обработчик команды /start
        self.application.add_handler(CommandHandler("start", self.start_command))
        
        # Команда /help удалена
        
        # ID группы задается в .env файле, команда /setgroup удалена
        
        # Обработчик текстовых сообщений (кроме команд и сообщений с медиа)
        self.application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.CAPTION, 
            self.forward_message
        ))
        
        # Обработчик медиафайлов (только фото)
        self.application.add_handler(MessageHandler(
            filters.PHOTO,
            self.forward_media
        ))
        
        # Обработчик callback кнопок
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        
        
        
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        welcome_message = f"""
🤖 Добро пожаловать в бота по отправке анонимных сообщений!

📖 Справка по боту:

🔧 Команды:
/start - начать работу с ботом

💡 Бот принимает:
• Текстовые сообщения
• Фото

⚠️ Ограничения:
• Максимум {DAILY_MESSAGE_LIMIT} сообщений в день на пользователя

Чат — https://t.me/spletnikolcovo

Ссылка для друга — https://t.me/vkolcovolubyate

‼️ПРАВИЛА‼️

1. Не оскорблять родственников 
2. Не осквернять религии 
3. Запрет на несанкционированную рекламу 

По вопросам рекламы к админу - @PabloAdmino

        """
        await update.message.reply_text(welcome_message)
    
    def load_data(self) -> dict:
        """Загружает данные из файла"""
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # После загрузки очищаем устаревшие записи и сохраняем при необходимости
                if self.prune_old_data(data):
                    self.save_data(data)
                return data
        except Exception as e:
            logger.error(f"Error loading data: {e}")
        return {}
    
    def save_data(self, data: dict):
        """Сохраняет данные в файл"""
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving data: {e}")

    def prune_old_data(self, data: dict) -> bool:
        """Удаляет данные старше 24 часов из JSON-структуры.
        Возвращает True, если были изменения."""
        changed = False

        try:
            # Очистка user_info по timestamp (строго старше 24 часов)
            if isinstance(data, dict) and 'user_info' in data and isinstance(data['user_info'], dict):
                now_ts = datetime.utcnow().timestamp()
                cutoff_ts = now_ts - 24 * 60 * 60
                keys_to_delete = []
                for key, value in list(data['user_info'].items()):
                    try:
                        if isinstance(value, dict) and float(value.get('timestamp', 0)) < cutoff_ts:
                            keys_to_delete.append(key)
                    except Exception:
                        # Если структура повреждена, удаляем запись
                        keys_to_delete.append(key)
                for key in keys_to_delete:
                    del data['user_info'][key]
                    changed = True

            # Очистка daily_limits по дате (удаляем даты старше чем 2 дня от текущей)
            # Так как daily_limits хранит агрегаты по дню, точность до суток приемлема.
            if isinstance(data, dict) and 'daily_limits' in data and isinstance(data['daily_limits'], dict):
                today = date.today()
                keys_to_delete = []
                for day_str in list(data['daily_limits'].keys()):
                    try:
                        day_obj = date.fromisoformat(day_str)
                        # Удаляем если дата старше 24 часов: строго меньше (today - 1 день)
                        if day_obj < (today - timedelta(days=1)):
                            keys_to_delete.append(day_str)
                    except Exception:
                        # Невалидная дата — удаляем
                        keys_to_delete.append(day_str)
                for key in keys_to_delete:
                    del data['daily_limits'][key]
                    changed = True
        except Exception as e:
            logger.error(f"Error pruning old data: {e}")

        return changed
    
    def check_daily_limit(self, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, int]:
        """Проверяет дневной лимит сообщений для пользователя"""
        today = date.today().isoformat()
        
        # Загружаем данные из файла
        data = self.load_data()
        
        # Инициализируем структуру данных для отслеживания лимитов
        if 'daily_limits' not in data:
            data['daily_limits'] = {}
        
        if today not in data['daily_limits']:
            data['daily_limits'][today] = {}
        
        # Получаем количество сообщений пользователя за сегодня
        user_messages = data['daily_limits'][today].get(str(user_id), 0)
        
        # Проверяем лимит
        if user_messages >= DAILY_MESSAGE_LIMIT:
            return False, user_messages
        
        return True, user_messages
    
    def increment_daily_count(self, user_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Увеличивает счетчик сообщений пользователя за день"""
        today = date.today().isoformat()
        
        # Загружаем данные из файла
        data = self.load_data()
        
        # Инициализируем структуру данных для отслеживания лимитов
        if 'daily_limits' not in data:
            data['daily_limits'] = {}
        
        if today not in data['daily_limits']:
            data['daily_limits'][today] = {}
        
        if str(user_id) not in data['daily_limits'][today]:
            data['daily_limits'][today][str(user_id)] = 0
        
        data['daily_limits'][today][str(user_id)] += 1
        
        # Сохраняем данные обратно в файл
        self.save_data(data)
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатия на кнопку"""
        query = update.callback_query
        await query.answer()
        
        # Парсим callback_data
        if query.data.startswith("info_"):
            parts = query.data.split("_")
            if len(parts) >= 3:
                user_id = parts[1]
                timestamp = parts[2]
                key = f"{user_id}_{timestamp}"
                
                # Загружаем данные из файла
                data = self.load_data()
                
                # Сначала пытаемся взять точное совпадение ключа из файла
                found_key = None
                if 'user_info' in data and key in data['user_info']:
                    found_key = key
                else:
                    # Ищем самый свежий ключ для этого пользователя
                    if 'user_info' in data:
                        user_keys = [k for k in data['user_info'].keys() if k.startswith(f"{user_id}_")]
                        if user_keys:
                            # берем запись с максимальным timestamp в ключе
                            try:
                                found_key = max(user_keys, key=lambda k: float(k.split("_")[1]))
                            except Exception:
                                found_key = user_keys[-1]
                
                # Получаем сохраненную информацию о пользователе
                logger.info(f"Looking for key: {key}")
                logger.info(f"Available keys: {list(data.get('user_info', {}).keys())}")
                logger.info(f"Found key: {found_key}")
                
                if found_key and 'user_info' in data and found_key in data['user_info']:
                    user_data = data['user_info'][found_key]
                    
                    # Формируем информацию об отправителе
                    sender_info = f"👤 От: {user_data['first_name']}"
                    if user_data['last_name']:
                        sender_info += f" {user_data['last_name']}"
                    if user_data['username']:
                        sender_info += f" (@{user_data['username']})"
                    # Всегда добавляем ID пользователя в той же строке
                    if user_data.get('user_id') is not None:
                        sender_info += f"      [ID: {user_data['user_id']}]"
                    
                    # Добавляем текст сообщения, только если он есть
                    if user_data.get('message_text'):
                        sender_info += f"\n💬 Сообщение: {user_data['message_text']}"
                    
                    # Форматируем время
                    from datetime import datetime
                    time_str = datetime.fromtimestamp(user_data['timestamp']).strftime('%d.%m.%Y %H:%M:%S')
                    sender_info += f"\n🕐 Время: {time_str}"
                    
                    # Отправляем информацию в группу
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=sender_info,
                        reply_to_message_id=query.message.message_id
                    )
                else:
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=f"❌ Информация об отправителе не найдена\nКлюч: {key}\nДоступные ключи: {list(data.get('user_info', {}).keys())}",
                        reply_to_message_id=query.message.message_id
                    )
    
    async def forward_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Пересылка текстовых сообщений"""
        await self.forward_to_group(update, context, "текстовое сообщение")
    
    async def forward_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Пересылка фото"""
        await self.forward_to_group(update, context, "фото")
    
    async def forward_to_group(self, update: Update, context: ContextTypes.DEFAULT_TYPE, message_type: str):
        """Основная функция пересылки сообщений в группу"""
        if not TARGET_GROUP_ID or TARGET_GROUP_ID == 'YOUR_GROUP_ID_HERE':
            await update.message.reply_text(
                "❌ Ошибка: ID группы не установлен!\n"
                "Установите TARGET_GROUP_ID в файле .env"
            )
            return
        
        # Получаем информацию об отправителе
        user = update.effective_user
        
        # Проверяем дневной лимит сообщений
        can_send, current_count = self.check_daily_limit(user.id, context)
        if not can_send:
            await update.message.reply_text(
                f"❌ Достигнут дневной лимит сообщений!\n"
                f"Вы отправили {current_count} из {DAILY_MESSAGE_LIMIT} сообщений сегодня.\n"
                f"Попробуйте завтра."
            )
            return
        
        try:
            # Получаем информацию о чате
            chat = update.effective_chat
            
            # Увеличиваем счетчик сообщений пользователя
            self.increment_daily_count(user.id, context)
            
            # Формируем информацию об отправителе
            sender_info = f"👤 От: {user.first_name}"
            if user.last_name:
                sender_info += f" {user.last_name}"
            if user.username:
                sender_info += f" (@{user.username})"
            
            
            sender_info += f"\n🕐 Время: {update.message.date.strftime('%d.%m.%Y %H:%M:%S')}"
            
            # Пересылаем сообщение в группу
            if update.message.text:
                # Сохраняем информацию о пользователе
                user_data = {
                    'user_id': user.id,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'username': user.username,
                    'message_text': update.message.text,
                    'timestamp': update.message.date.timestamp()
                }
                
                # Создаем кнопку "Узнать информацию"
                keyboard = [[InlineKeyboardButton("🔍 Узнать информацию", callback_data=f"info_{user.id}_{update.message.date.timestamp()}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Отправляем текст сообщения с кнопкой
                await context.bot.send_message(
                    chat_id=TARGET_GROUP_ID,
                    text=update.message.text,
                    reply_markup=reply_markup
                )
                
                # Сохраняем данные пользователя в файл
                data = self.load_data()
                if 'user_info' not in data:
                    data['user_info'] = {}
                key = f"{user.id}_{update.message.date.timestamp()}"
                data['user_info'][key] = user_data
                self.save_data(data)
                logger.info(f"Saved user data with key: {key}")
                logger.info(f"User data: {user_data}")
            else:
                # Медиафайл - отправляем как новое сообщение (анонимно)
                if update.message.photo:
                    # Сохраняем информацию о пользователе
                    user_data = {
                        'user_id': user.id,
                        'first_name': user.first_name,
                        'last_name': user.last_name,
                        'username': user.username,
                        'message_text': update.message.caption if update.message.caption else None,
                        'timestamp': update.message.date.timestamp()
                    }
                    
                    # Создаем кнопку "Узнать информацию"
                    keyboard = [[InlineKeyboardButton("🔍 Узнать информацию", callback_data=f"info_{user.id}_{update.message.date.timestamp()}")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    # Отправляем фото с кнопкой и подписью (если есть)
                    caption = update.message.caption if update.message.caption else None
                    await context.bot.send_photo(
                        chat_id=TARGET_GROUP_ID,
                        photo=update.message.photo[-1].file_id,
                        caption=caption,
                        reply_markup=reply_markup
                    )
                    
                    # Сохраняем данные пользователя в файл
                    data = self.load_data()
                    if 'user_info' not in data:
                        data['user_info'] = {}
                    key = f"{user.id}_{update.message.date.timestamp()}"
                    data['user_info'][key] = user_data
                    self.save_data(data)
                    logger.info(f"Saved user data with key: {key}")
                    logger.info(f"User data: {user_data}")
                
            
            # Подтверждение пользователю с информацией о лимите
            remaining = DAILY_MESSAGE_LIMIT - (current_count + 1)
            if message_type == "текстовое сообщение":
                await update.message.reply_text(
                    f"✅ Текстовое сообщение отправлено!\n"
                    f"📊 Осталось сообщений сегодня: {remaining}"
                )
            else:
                await update.message.reply_text(
                    f"✅ Фото отправлено!\n"
                    f"📊 Осталось сообщений сегодня: {remaining}"
                )
            
            logger.info(f"Message forwarded from {user.id} to group {TARGET_GROUP_ID}")
            
        except TelegramError as e:
            error_message = f"❌ Ошибка при пересылке: {str(e)}"
            await update.message.reply_text(error_message)
            logger.error(f"Telegram error: {e}")
            
        except Exception as e:
            error_message = f"❌ Неожиданная ошибка: {str(e)}"
            await update.message.reply_text(error_message)
            logger.error(f"Unexpected error: {e}")
    
    def run(self):
        """Запуск бота"""
        logger.info("Starting bot...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES, timeout=30, poll_interval=0)

def main():
    """Основная функция"""
    # Получаем токен бота из переменной окружения
    bot_token = os.getenv('BOT_TOKEN')
    
    if not bot_token:
        print("❌ Ошибка: Не установлен токен бота!")
        print("Установите переменную окружения BOT_TOKEN или создайте файл config.env")
        return
    
    # Создаем и запускаем бота
    bot = MessageForwarderBot(bot_token)
    bot.run()

if __name__ == '__main__':
    main()

