import os
import logging
import sqlite3
import asyncio
import json
import traceback
import re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, InputMediaPhoto, Chat
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, ConversationHandler
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Constants
BOT_TOKEN = "7718652754:AAG9x_liYhxqXbfYuNOe0NtACraLE0fsNTo"
DATABASE_CHANNEL_ID = -1002556064397
SEARCH_LIMIT = 200
SEARCH_TIMEOUT = 30
ADMIN_USER_IDS = [6016330931, 1226889502]  # Admin user ID
IMAGE_COST = 1  # Points per image
POINT_VALUE = 10  # 1 point = 10 INR
REFERRAL_REWARD = 0.1  # Points for each referral
ADMIN_USERNAME = "@darkvipin"
ADMIN_WHATSAPP = "+91 81888 16160"
MIN_RECHARGE = 10  # Minimum recharge points
SOURCE_CHANNEL_ID = -1002350278839  # Source channel for automatic indexing

# Conversation states
SEARCHING, COLLECTING_FILES, CONFIRMING_PURCHASE = range(3)

# Store user states and collected files
user_states = {}
collected_files = {}
pending_purchases = {}  # Store pending image purchases
user_join_status = {}  # Track if user has been logged as joined

# Initialize database with new tables
def init_db():
    """Initialize the database with required tables."""
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        # First, drop the existing images table to recreate with correct schema
        cursor.execute('DROP TABLE IF EXISTS images')

        # Create images table with all required columns
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY,
                file_id TEXT NOT NULL UNIQUE,
                file_name TEXT NOT NULL,
                image_id TEXT,
                file_size INTEGER,
                mime_type TEXT,
                message_id INTEGER,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create indexing status table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS indexing_status (
                id INTEGER PRIMARY KEY,
                total_files INTEGER DEFAULT 0,
                last_indexed TIMESTAMP,
                status TEXT DEFAULT 'idle'
            )
        ''')

        # Create user stats table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY,
                total_searches INTEGER DEFAULT 0,
                total_downloads INTEGER DEFAULT 0,
                last_search TIMESTAMP,
                downloads INTEGER DEFAULT 0
            )
        ''')

        # Create user balance table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_balance (
                user_id INTEGER PRIMARY KEY,
                balance REAL DEFAULT 0,
                total_spent REAL DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create transactions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                type TEXT,
                description TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create search history table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                image_id TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        logger.info("Database initialized successfully with updated schema")
    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")
        raise
    finally:
        conn.close()

# Initialize database
init_db()

def extract_image_id(text):
    """Extract image ID from text or URL."""
    # Try to find ID in URL pattern
    url_pattern = r'shutterstock\.com.*?(\d{6,})'
    match = re.search(url_pattern, text)
    if match:
        return match.group(1)

    # Try to find standalone ID
    id_pattern = r'(\d{6,})'
    match = re.search(id_pattern, text)
    if match:
        return match.group(1)

    return None

async def send_log_to_channel(context: ContextTypes.DEFAULT_TYPE, log_message: str):
    """Send a log message to the database channel."""
    try:
        await context.bot.send_message(
            chat_id=DATABASE_CHANNEL_ID,
            text=log_message
        )
    except Exception as e:
        logger.error(f"Error sending log to channel: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    try:
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            user_id = query.from_user.id
            message = query.message
        else:
            user_id = update.effective_user.id
            message = update.message

        # Reset user state
        user_states[user_id] = {'searching': False}

        # Check for referral
        if context.args and not update.callback_query:
            referrer_id = int(context.args[0])
            if referrer_id != user_id:  # Prevent self-referral
                await handle_referral(user_id, referrer_id, update)

        # Create main menu keyboard
        keyboard = [
            [InlineKeyboardButton("🔍 Search Image", callback_data='search')],
            [InlineKeyboardButton("💰 Balance & Points", callback_data='balance_menu')],
            [InlineKeyboardButton("👥 Refer & Earn", callback_data='referral')],
            [InlineKeyboardButton("❓ Help", callback_data='help')],
            [InlineKeyboardButton("📞 Contact Admin", callback_data='contact_admin')]
        ]

        # Add Admin Panel button if user is admin
        if user_id in ADMIN_USER_IDS:
            keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data='admin_panel')])

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Send welcome message with features
        welcome_text = (
            "👋 *Welcome to the Image Search Bot!*\n\n"
            "📝 *Features:*\n"
            "• Search and download images\n"
            "• Point-based system (1 point = 10 INR)\n"
            "• Referral rewards (0.1 points per referral)\n"
            "• Balance management\n"
            "• Admin controls\n\n"
            "💎 *Pricing:*\n"
            "• 1 Image = 1 Point\n"
            "• 1 Point = 10 INR\n"
            "• Minimum recharge: 10 points\n\n"
            "🎁 *Referral Program:*\n"
            "• Share your referral link\n"
            "• Get 0.1 points for each new user\n"
            "• No limit on referrals\n\n"
            "💡 *How to use:*\n"
            "1. Click 'Search Image' or use /search\n"
            "2. Enter image ID or URL\n"
            "3. If found, confirm purchase\n"
            "4. Image will be sent after payment\n\n"
            "Need help? Click 'Contact Admin'!"
        )

        if update.callback_query:
            await message.edit_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

        # Send log to channel only if user hasn't been logged before
        if user_id not in user_join_status:
            user_join_status[user_id] = True
            await send_log_to_channel(
                context,
                f"New User Joined\n"
                f"User ID: {user_id}\n"
                f"Username: @{update.effective_user.username if update.effective_user.username else 'N/A'}\n"
                f"Name: {update.effective_user.first_name} {update.effective_user.last_name if update.effective_user.last_name else ''}"
            )

    except Exception as e:
        logger.error(f"Error in start command: {str(e)}")
        if update.callback_query:
            await update.callback_query.message.reply_text("An error occurred. Please try again.")
        else:
            await update.message.reply_text("An error occurred. Please try again.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message."""
    try:
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            message = query.message
        else:
            message = update.message

        keyboard = [
            [InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        help_text = (
            "🤖 *Bot Commands*\n\n"
            "🔍 *Search & Download*\n"
            "• /search - Search for an image\n"
            "• Send image ID or URL to download\n\n"
            "💰 *Balance & Points*\n"
            "• /balance - Check your balance\n"
            "• /addbalance - Add points to your account\n\n"
            "👥 *Referral Program*\n"
            "• Share your referral link\n"
            "• Earn 0.1 points per successful referral\n\n"
            "👑 *Admin Commands*\n"
            "• /stats - View bot statistics\n"
            "• /broadcast - Send message to all users\n"
            "• /editbalance - Edit user balance\n"
            "• /checkbalance - Check user balance\n\n"
            "❓ *Need Help?*\n"
            "Contact admin for assistance."
        )

        if update.callback_query:
            await message.edit_text(help_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await message.reply_text(help_text, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in help command: {str(e)}")
        if update.callback_query:
            await update.callback_query.message.reply_text("An error occurred while showing help. Please try again.")
        else:
            await update.message.reply_text("An error occurred while showing help. Please try again.")

async def handle_referral(user_id: int, referrer_id: int, update: Update):
    """Handle referral rewards."""
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        # Create referrals table if it doesn't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                referrer_id INTEGER,
                referred_id INTEGER,
                reward_given INTEGER DEFAULT 1,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (referrer_id, referred_id)
            )
        ''')
        conn.commit()

        # Check if referral already exists
        cursor.execute('SELECT * FROM referrals WHERE referred_id = ?', (user_id,))
        if cursor.fetchone():
            return

        # Add referral record
        cursor.execute('''
            INSERT INTO referrals (referrer_id, referred_id, reward_given)
            VALUES (?, ?, 1)
        ''', (referrer_id, user_id))

        # Update referrer's balance
        cursor.execute('''
            UPDATE user_balance
            SET balance = balance + ?
            WHERE user_id = ?
        ''', (REFERRAL_REWARD, referrer_id))

        # Add transaction record
        cursor.execute('''
            INSERT INTO transactions (user_id, amount, type, description)
            VALUES (?, ?, 'REFERRAL', 'Referral reward')
        ''', (referrer_id, REFERRAL_REWARD))

        conn.commit()

        # Notify both users
        await update.message.reply_text(
            f"🎉 Welcome! You were referred by user {referrer_id}.\n"
            f"They received {REFERRAL_REWARD} points as a reward!"
        )

        try:
            await context.bot.send_message(
                chat_id=referrer_id,
                text=f"🎉 You received {REFERRAL_REWARD} points for referring user {user_id}!"
            )
        except Exception as e:
            logger.error(f"Error sending referral notification: {str(e)}")

    except Exception as e:
        logger.error(f"Error in handle_referral: {str(e)}")
    finally:
        conn.close()

async def balance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show balance menu with options."""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        # Get user's balance
        cursor.execute('SELECT balance, total_spent FROM user_balance WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        balance = result[0] if result else 0
        total_spent = result[1] if result else 0

        # Create keyboard
        keyboard = [
            [InlineKeyboardButton("💳 Add Points", callback_data='add_points')],
            [InlineKeyboardButton("📊 Transaction History", callback_data='transactions')],
            [InlineKeyboardButton("👥 My Referrals", callback_data='my_referrals')],
            [InlineKeyboardButton("🔙 Back", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Create balance text
        balance_text = (
            f"💰 *Your Balance*\n\n"
            f"Available Points: {balance}\n"
            f"Total Spent: {total_spent}\n"
            f"1 Point = {POINT_VALUE} INR\n\n"
            f"*Pricing:*\n"
            f"• 1 Image = 1 Point\n"
            f"• Minimum recharge: {MIN_RECHARGE} points\n\n"
            f"Select an option below:"
        )

        await query.message.edit_text(balance_text, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in balance_menu: {str(e)}")
        await query.message.reply_text("An error occurred. Please try again.")
    finally:
        conn.close()

async def referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show referral menu."""
    try:
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            user_id = query.from_user.id
            message = query.message
        else:
            user_id = update.effective_user.id
            message = update.message

        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        # Create referrals table if it doesn't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                referrer_id INTEGER,
                referred_id INTEGER,
                reward_given INTEGER DEFAULT 1,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (referrer_id, referred_id)
            )
        ''')
        conn.commit()

        # Get referral stats
        cursor.execute('''
            SELECT COUNT(*),
                   SUM(reward_given)
            FROM referrals
            WHERE referrer_id = ?
        ''', (user_id,))

        result = cursor.fetchone()
        total_referrals = result[0] or 0
        successful_referrals = result[1] or 0

        # Generate referral link
        bot_username = (await context.bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start={user_id}"

        keyboard = [
            [InlineKeyboardButton("📤 Share Referral Link", url=f"https://t.me/share/url?url={referral_link}")],
            [InlineKeyboardButton("📊 Referral History", callback_data='my_referrals')],
            [InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            f"👥 *Refer & Earn*\n\n"
            f"*Your Referral Stats:*\n"
            f"Total Referrals: {total_referrals}\n"
            f"Successful Referrals: {successful_referrals}\n"
            f"Points Earned: {successful_referrals * REFERRAL_REWARD}\n\n"
            f"*How it works:*\n"
            f"1. Share your referral link\n"
            f"2. When someone joins using your link\n"
            f"3. You get {REFERRAL_REWARD} points\n"
            f"4. No limit on referrals!\n\n"
            f"*Your Referral Link:*\n"
            f"`{referral_link}`"
        )

        if update.callback_query:
            await message.edit_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in referral_menu: {str(e)}")
        error_message = "An error occurred. Please try again."
        if update.callback_query:
            await update.callback_query.message.reply_text(error_message)
        else:
            await update.message.reply_text(error_message)
    finally:
        conn.close()

async def contact_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show contact options for admin."""
    try:
        query = update.callback_query
        await query.answer()

        keyboard = [
            [InlineKeyboardButton("📱 Telegram", url=f"https://t.me/{ADMIN_USERNAME[1:]}")],
            [InlineKeyboardButton("📞 WhatsApp", url=f"https://wa.me/{ADMIN_WHATSAPP.replace(' ', '')}")],
            [InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        contact_text = (
            "📞 *Contact Admin*\n\n"
            "Choose your preferred contact method:\n\n"
            f"• Telegram: {ADMIN_USERNAME}\n"
            f"• WhatsApp: {ADMIN_WHATSAPP}\n\n"
            "For:\n"
            "• Adding points to your account\n"
            "• Reporting issues\n"
            "• General inquiries"
        )

        await query.message.edit_text(contact_text, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in contact_admin: {str(e)}")
        await query.message.reply_text("An error occurred. Please try again.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses."""
    try:
        query = update.callback_query
        if not query:
            return

        await query.answer()  # Answer the callback query to remove loading state
        user_id = query.from_user.id
        data = query.data

        logger.info(f"Button pressed by user {user_id}: {data}")  # Add logging

        if data == 'back_to_main':
            await start(update, context)

        elif data == 'balance_menu':
            await balance_menu(update, context)

        elif data == 'add_points':
            await addbalance_command(update, context)

        elif data == 'transactions':
            await show_transactions(update, context)

        elif data == 'my_referrals':
            await show_referrals(update, context)

        elif data == 'referral':
            await referral_menu(update, context)

        elif data == 'help':
            await help_command(update, context)

        elif data == 'admin_panel' and user_id in ADMIN_USER_IDS:
            await admin_panel(update, context)

        elif data == 'contact_admin':
            await contact_admin(update, context)

        elif data == 'search':
            await search_command(update, context)

        elif data.startswith('confirm_'):
            image_id = data.split('_')[1]
            await confirm_purchase(update, context, image_id)

        elif data == 'cancel_purchase':
            await cancel_purchase(update, context)

    except Exception as e:
        logger.error(f"Error in button handler: {str(e)}")
        try:
            await query.message.reply_text("❌ An error occurred. Please try again.")
        except:
            pass

async def show_transactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's transaction history."""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        # Get last 10 transactions
        cursor.execute('''
            SELECT amount, type, description, timestamp
            FROM transactions
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT 10
        ''', (user_id,))

        transactions = cursor.fetchall()

        if not transactions:
            text = "📊 *Transaction History*\n\nNo transactions found."
        else:
            text = "📊 *Transaction History*\n\n"
            for amount, type_, desc, timestamp in transactions:
                text += f"• {timestamp}: {type_} - {amount} points\n"
                text += f"  {desc}\n\n"

        # Add back button
        keyboard = [[InlineKeyboardButton("🔙 Back to Balance", callback_data='balance_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(text, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in show_transactions: {str(e)}")
        await query.message.reply_text("An error occurred while fetching transactions.")
    finally:
        conn.close()

async def show_referrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's referral history."""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        # Create referrals table if it doesn't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                referrer_id INTEGER,
                referred_id INTEGER,
                reward_given INTEGER DEFAULT 1,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (referrer_id, referred_id)
            )
        ''')
        conn.commit()

        # Get total referrals and rewards
        cursor.execute('''
            SELECT COUNT(*),
                   SUM(reward_given)
            FROM referrals
            WHERE referrer_id = ?
        ''', (user_id,))

        result = cursor.fetchone()
        total_referrals = result[0] or 0
        successful_referrals = result[1] or 0

        # Get recent referrals
        cursor.execute('''
            SELECT referred_id, timestamp, reward_given
            FROM referrals
            WHERE referrer_id = ?
            ORDER BY timestamp DESC
            LIMIT 10
        ''', (user_id,))

        referrals = cursor.fetchall()

        text = (
            f"👥 *Referral History*\n\n"
            f"Total Referrals: {total_referrals}\n"
            f"Successful Referrals: {successful_referrals}\n"
            f"Points Earned: {successful_referrals * REFERRAL_REWARD}\n\n"
        )

        if referrals:
            text += "*Recent Referrals:*\n"
            for referred_id, timestamp, reward_given in referrals:
                status = "✅ Rewarded" if reward_given else "⏳ Pending"
                text += f"• User {referred_id} - {status}\n"
        else:
            text += "No referrals yet. Share your referral link to earn points!"

        # Add back button
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='referral')]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(text, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in show_referrals: {str(e)}")
        await query.message.reply_text("An error occurred while fetching referrals.")
    finally:
        conn.close()

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin panel."""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        if user_id not in ADMIN_USER_IDS:
            await query.message.reply_text("❌ You don't have permission to access the admin panel.")
            return

        keyboard = [
            [InlineKeyboardButton("📊 Statistics", callback_data='stats')],
            [InlineKeyboardButton("📢 Broadcast", callback_data='broadcast')],
            [InlineKeyboardButton("💰 Balance Management", callback_data='balance_management')],
            [InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            "👑 *Admin Panel*\n\n"
            "Select an option below:"
        )

        await query.message.edit_text(text, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in admin_panel: {str(e)}")
        await query.message.reply_text("An error occurred while accessing the admin panel.")

async def confirm_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE, image_id: str):
    """Handle purchase confirmation."""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        if user_id not in pending_purchases:
            await query.message.edit_text("❌ Purchase expired. Please try again.")
            return

        purchase = pending_purchases[user_id]
        if purchase['image_id'] != image_id:
            await query.message.edit_text("❌ Invalid purchase. Please try again.")
            return

        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        # Check balance again
        cursor.execute('SELECT balance FROM user_balance WHERE user_id = ?', (user_id,))
        balance = cursor.fetchone()[0]

        if balance < IMAGE_COST:
            await query.message.edit_text(
                f"❌ Insufficient balance.\n\n"
                f"Required: {IMAGE_COST} point\n"
                f"Your balance: {balance} points\n\n"
                f"Use /addbalance to add more points."
            )
            return

        # Deduct points
        cursor.execute('''
            UPDATE user_balance
            SET balance = balance - ?,
                total_spent = total_spent + ?,
                last_updated = CURRENT_TIMESTAMP
            WHERE user_id = ?
        ''', (IMAGE_COST, IMAGE_COST, user_id))

        # Record transaction
        cursor.execute('''
            INSERT INTO transactions (user_id, amount, type, description)
            VALUES (?, ?, 'PURCHASE', ?)
        ''', (user_id, -IMAGE_COST, f'Purchased image {image_id}'))

        conn.commit()

        # Send the image
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=purchase['file_id']
        )

        await query.message.edit_text(
            f"✅ Purchase successful!\n"
            f"Image ID: {image_id}\n"
            f"Cost: {IMAGE_COST} point\n"
            f"Remaining balance: {balance - IMAGE_COST} points"
        )

        # Send log to channel
        await send_log_to_channel(
            context,
            f"Image Purchase\n"
            f"User ID: {user_id}\n"
            f"Username: @{update.effective_user.username if update.effective_user.username else 'N/A'}\n"
            f"Image ID: {image_id}\n"
            f"Cost: {IMAGE_COST} point\n"
            f"Remaining Balance: {balance - IMAGE_COST} points"
        )

        # Clear pending purchase
        del pending_purchases[user_id]

    except Exception as e:
        logger.error(f"Error in confirm_purchase: {str(e)}")
        await query.message.edit_text("❌ An error occurred while processing your purchase.")
    finally:
        conn.close()

async def cancel_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle purchase cancellation."""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        if user_id in pending_purchases:
            del pending_purchases[user_id]

        await query.message.edit_text("❌ Purchase cancelled.")

    except Exception as e:
        logger.error(f"Error in cancel_purchase: {str(e)}")
        await query.message.reply_text("An error occurred while cancelling the purchase.")

async def post_init(application: Application):
    """Post initialization setup."""
    try:
        await application.bot.set_my_commands([
            BotCommand("start", "Start the bot"),
            BotCommand("help", "Show help message"),
            BotCommand("balance", "Check your balance"),
            BotCommand("addbalance", "Add points to your account"),
            BotCommand("search", "Search for an image"),
            BotCommand("index", "Start collecting files for indexing"),
            BotCommand("indexdone", "Finish indexing and save files"),
            BotCommand("stats", "Show indexing statistics"),
            BotCommand("editbalance", "Edit user balance (Admin)"),
            BotCommand("checkbalance", "Check user balance (Admin)"),
            BotCommand("broadcast", "Start broadcast message process")
        ])
        logger.info("Bot commands set successfully")
    except Exception as e:
        logger.error(f"Error in post_init: {str(e)}")

async def index_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start collecting files for indexing."""
    try:
        user_id = update.effective_user.id
        if user_id not in ADMIN_USER_IDS:
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return

        # Initialize file collection
        collected_files[user_id] = []
        user_states[user_id] = {'collecting_files': True}

        await update.message.reply_text(
            "🔄 Starting file collection mode...\n\n"
            "Please send me the files you want to index.\n"
            "When you're done, send /indexdone to save all files.\n"
            "To cancel, send /cancel"
        )
        logger.info(f"User {user_id} started file collection")

    except Exception as e:
        logger.error(f"Error in index command: {str(e)}")
        await update.message.reply_text("An error occurred while starting file collection. Please try again.")

async def index_done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finish indexing and save all collected files."""
    try:
        user_id = update.effective_user.id
        if user_id not in ADMIN_USER_IDS:
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return

        if user_id not in collected_files or not collected_files[user_id]:
            await update.message.reply_text("❌ No files have been collected. Use /index to start collecting files.")
            return

        status_msg = await update.message.reply_text("🔄 Saving collected files to database...")

        try:
            conn = sqlite3.connect('bot_database.db')
            cursor = conn.cursor()

            saved_count = 0
            for file_info in collected_files[user_id]:
                try:
                    image_id = file_info['file_name'].split('_')[1].split('.')[0]
                    cursor.execute('''
                        INSERT OR REPLACE INTO images
                        (image_id, file_id, file_name, message_id)
                        VALUES (?, ?, ?, ?)
                    ''', (image_id, file_info['file_id'], file_info['file_name'], file_info['message_id']))
                    saved_count += 1
                except Exception as e:
                    logger.error(f"Error saving file {file_info['file_name']}: {str(e)}")

            conn.commit()
            conn.close()

            # Clear collected files
            collected_files[user_id] = []
            user_states[user_id] = {'collecting_files': False}

            await status_msg.edit_text(f"✅ Successfully saved {saved_count} files to the database!")
            logger.info(f"User {user_id} saved {saved_count} files to database")

        except Exception as e:
            logger.error(f"Error saving files to database: {str(e)}")
            await status_msg.edit_text("❌ An error occurred while saving files to the database.")

    except Exception as e:
        logger.error(f"Error in index_done command: {str(e)}")
        await update.message.reply_text("An error occurred while saving files. Please try again.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors."""
    try:
        logger.error(f"Update {update} caused error {context.error}")
        if update and update.effective_message:
            await update.effective_message.reply_text(
                'Sorry, an error occurred. Please try again later.'
            )
    except Exception as e:
        logger.error(f"Error in error handler: {str(e)}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show indexing statistics."""
    try:
        user_id = update.effective_user.id
        if user_id not in ADMIN_USER_IDS:
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return

        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        # Get total images
        cursor.execute('SELECT COUNT(*) FROM images')
        total_images = cursor.fetchone()[0]

        # Get total users
        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM user_balance')
        total_users = cursor.fetchone()[0]

        # Get total searches
        cursor.execute('SELECT COUNT(*) FROM search_history')
        total_searches = cursor.fetchone()[0]

        # Get total downloads
        cursor.execute('SELECT COUNT(*) FROM transactions WHERE type = "PURCHASE"')
        total_downloads = cursor.fetchone()[0]

        # Get total points spent
        cursor.execute('SELECT SUM(ABS(amount)) FROM transactions WHERE type = "PURCHASE"')
        total_points_spent = cursor.fetchone()[0] or 0

        # Get last indexed time
        cursor.execute('SELECT MAX(added_date) FROM images')
        last_indexed = cursor.fetchone()[0] or "Never"

        stats_text = (
            f"📊 *Bot Statistics*\n\n"
            f"Total Images: {total_images}\n"
            f"Total Users: {total_users}\n"
            f"Total Searches: {total_searches}\n"
            f"Total Downloads: {total_downloads}\n"
            f"Total Points Spent: {total_points_spent}\n"
            f"Last Indexed: {last_indexed}\n\n"
            f"Use /index to start collecting files"
        )

        await update.message.reply_text(stats_text, parse_mode='Markdown')
        logger.info(f"Stats command executed for user {user_id}")

    except Exception as e:
        logger.error(f"Error in stats command: {str(e)}")
        await update.message.reply_text("An error occurred while fetching statistics. Please try again.")
    finally:
        conn.close()

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the search process."""
    try:
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            user_id = query.from_user.id
            message = query.message
        else:
            user_id = update.effective_user.id
            message = update.message

        # Set user state to searching
        user_states[user_id] = {'searching': True}

        keyboard = [
            [InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        search_text = (
            "🔍 *Search for Images*\n\n"
            "Please send the image ID or Shutterstock URL.\n\n"
            "Examples:\n"
            "• 2301326979\n"
            "• https://www.shutterstock.com/image-vector/...2301326979\n\n"
            "Click 'Back to Main' to cancel search."
        )

        if update.callback_query:
            await message.edit_text(search_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await message.reply_text(search_text, reply_markup=reply_markup, parse_mode='Markdown')

        logger.info(f"User {user_id} started searching")

    except Exception as e:
        logger.error(f"Error in search command: {str(e)}")
        if update.callback_query:
            await query.message.reply_text("An error occurred. Please try again.")
        else:
            await update.message.reply_text("An error occurred. Please try again.")

async def handle_image_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle image ID search and purchase."""
    try:
        user_id = update.effective_user.id

        # Extract image ID from text
        image_id = extract_image_id(update.message.text)
        if not image_id:
            await update.message.reply_text(
                "❌ Invalid image ID or URL format.\n\n"
                "Please send a valid Shutterstock image ID or URL.\n"
                "Example: 2301326979 or https://www.shutterstock.com/image-vector/...2301326979"
            )
            return

        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        # Check if image exists
        cursor.execute('''
            SELECT file_id, file_name
            FROM images
            WHERE image_id = ?
        ''', (image_id,))

        result = cursor.fetchone()
        if not result:
            # Send log to channel for not found image
            await send_log_to_channel(
                context,
                f"Image Not Found\n"
                f"User ID: {user_id}\n"
                f"Username: @{update.effective_user.username if update.effective_user.username else 'N/A'}\n"
                f"Image ID/Link: {image_id}"
            )
            await update.message.reply_text(
                "❌ Image not found in database.\n\n"
                "Please check the ID and try again."
            )
            return

        file_id, file_name = result

        # Check user's balance
        cursor.execute('''
            SELECT balance
            FROM user_balance
            WHERE user_id = ?
        ''', (user_id,))

        result = cursor.fetchone()
        balance = result[0] if result else 0

        if balance < IMAGE_COST:
            await update.message.reply_text(
                f"❌ Insufficient balance.\n\n"
                f"Required: {IMAGE_COST} point\n"
                f"Your balance: {balance} points\n\n"
                f"Use /addbalance to add more points."
            )
            return

        # Create purchase confirmation keyboard
        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm Purchase", callback_data=f'confirm_{image_id}'),
                InlineKeyboardButton("❌ Cancel", callback_data='cancel_purchase')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Store pending purchase
        pending_purchases[user_id] = {
            'image_id': image_id,
            'file_id': file_id,
            'file_name': file_name
        }

        # Send purchase confirmation
        await update.message.reply_text(
            f"🛒 *Purchase Confirmation*\n\n"
            f"Image ID: {image_id}\n"
            f"Cost: {IMAGE_COST} point\n"
            f"Your balance: {balance} points\n\n"
            f"Click 'Confirm Purchase' to proceed.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

        # Reset searching state
        user_states[user_id]['searching'] = False

    except Exception as e:
        logger.error(f"Error in handle_image_id: {str(e)}")
        await update.message.reply_text("An error occurred while processing your request. Please try again.")
    finally:
        conn.close()

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file uploads during indexing."""
    try:
        user_id = update.effective_user.id
        if user_id not in ADMIN_USER_IDS:
            return

        state = user_states.get(user_id, {})
        if not state.get('collecting_files'):
            return

        # Get file information
        file = update.message.document
        if not file:
            await update.message.reply_text("❌ Please send a valid file.")
            return

        # Validate file name format
        file_name = file.file_name
        if not file_name or not file_name.startswith('shutterstock_'):
            await update.message.reply_text(
                "❌ Invalid file name format.\n\n"
                "File name must start with 'shutterstock_' followed by the image ID.\n"
                "Example: shutterstock_2301326979.jpg"
            )
            return

        # Extract image ID from file name
        try:
            image_id = file_name.split('_')[1].split('.')[0]
        except IndexError:
            await update.message.reply_text(
                "❌ Invalid file name format.\n\n"
                "Could not extract image ID from file name.\n"
                "Example: shutterstock_2301326979.jpg"
            )
            return

        # Store file information
        if user_id not in collected_files:
            collected_files[user_id] = []

        collected_files[user_id].append({
            'file_id': file.file_id,
            'file_name': file_name,
            'image_id': image_id
        })

        # Send confirmation
        await update.message.reply_text(
            f"✅ File received and stored.\n\n"
            f"File: {file_name}\n"
            f"Image ID: {image_id}\n\n"
            f"Send more files or use /indexdone to finish indexing."
        )
        logger.info(f"File {file_name} received from user {user_id}")

    except Exception as e:
        logger.error(f"Error in handle_file: {str(e)}")
        await update.message.reply_text("An error occurred while processing the file. Please try again.")

async def balance_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show balance management options for admin."""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        if user_id not in ADMIN_USER_IDS:
            await query.message.reply_text("❌ You don't have permission to access this feature.")
            return

        keyboard = [
            [InlineKeyboardButton("➕ Add Balance", callback_data='add_balance_admin')],
            [InlineKeyboardButton("➖ Remove Balance", callback_data='remove_balance_admin')],
            [InlineKeyboardButton("📊 Check Balance", callback_data='check_balance_admin')],
            [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data='admin_panel')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            "💰 *Balance Management*\n\n"
            "Select an option below:"
        )

        await query.message.edit_text(text, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in balance_management: {str(e)}")
        await query.message.reply_text("An error occurred. Please try again.")

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check user's balance."""
    try:
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            user_id = query.from_user.id
            message = query.message
        else:
            user_id = update.effective_user.id
            message = update.message

        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        # Get user's balance
        cursor.execute('''
            SELECT balance, total_spent
            FROM user_balance
            WHERE user_id = ?
        ''', (user_id,))

        result = cursor.fetchone()
        if result:
            balance, total_spent = result
        else:
            balance, total_spent = 0, 0
            cursor.execute('''
                INSERT INTO user_balance (user_id, balance, total_spent)
                VALUES (?, 0, 0)
            ''', (user_id,))
            conn.commit()

        # Create keyboard
        keyboard = [
            [InlineKeyboardButton("💳 Add Points", callback_data='add_points')],
            [InlineKeyboardButton("📊 Transaction History", callback_data='transactions')],
            [InlineKeyboardButton("👥 My Referrals", callback_data='my_referrals')],
            [InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        balance_text = (
            f"💰 *Your Balance*\n\n"
            f"Available Points: {balance}\n"
            f"Total Spent: {total_spent}\n"
            f"1 Point = {POINT_VALUE} INR\n\n"
            f"*Pricing:*\n"
            f"• 1 Image = 1 Point\n"
            f"• Minimum recharge: {MIN_RECHARGE} points\n\n"
            f"Select an option below:"
        )

        if update.callback_query:
            await message.edit_text(balance_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await message.reply_text(balance_text, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in balance command: {str(e)}")
        if update.callback_query:
            await update.callback_query.message.reply_text("An error occurred while checking balance. Please try again.")
        else:
            await update.message.reply_text("An error occurred while checking balance. Please try again.")
    finally:
        conn.close()

async def addbalance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show instructions for adding balance."""
    try:
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            message = query.message
        else:
            message = update.message

        keyboard = [
            [InlineKeyboardButton("📱 Contact Admin", url=f"https://t.me/{ADMIN_USERNAME[1:]}")],
            [InlineKeyboardButton("📞 WhatsApp", url=f"https://wa.me/{ADMIN_WHATSAPP.replace(' ', '')}")],
            [InlineKeyboardButton("🔙 Back to Balance", callback_data='balance_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        addbalance_text = (
            "💰 *Add Balance*\n\n"
            "To add points to your account:\n"
            "1. Click 'Contact Admin' below\n"
            "2. Choose your preferred contact method\n"
            "3. Make the payment\n"
            "4. Admin will add points to your account\n\n"
            "*Pricing:*\n"
            f"• 1 Point = {POINT_VALUE} INR\n"
            f"• Minimum recharge: {MIN_RECHARGE} points\n"
            "• No maximum limit"
        )

        if update.callback_query:
            await message.edit_text(addbalance_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await message.reply_text(addbalance_text, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in addbalance_command: {str(e)}")
        if update.callback_query:
            await update.callback_query.message.reply_text("An error occurred. Please try again.")
        else:
            await update.message.reply_text("An error occurred. Please try again.")

async def editbalance_command(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int = None, amount: int = None, action: str = None):
    """Edit user's balance (admin only)."""
    try:
        user_id = update.effective_user.id
        if user_id not in ADMIN_USER_IDS:
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return

        if not target_user_id or not amount:
            # If called directly, ask for user ID and amount
            if not context.args or len(context.args) < 2:
                await update.message.reply_text(
                    "❌ Please provide user ID and amount.\n"
                    "Usage: /editbalance <user_id> <amount>"
                )
                return

            try:
                target_user_id = int(context.args[0])
                amount = int(context.args[1])
            except ValueError:
                await update.message.reply_text("❌ Invalid user ID or amount. Please provide numbers only.")
                return

        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        # Get current balance
        cursor.execute('''
            SELECT balance
            FROM user_balance
            WHERE user_id = ?
        ''', (target_user_id,))

        result = cursor.fetchone()
        if not result:
            # Create new balance record if user doesn't exist
            cursor.execute('''
                INSERT INTO user_balance (user_id, balance, total_spent)
                VALUES (?, 0, 0)
            ''', (target_user_id,))
            current_balance = 0
        else:
            current_balance = result[0]

        # Update balance
        new_balance = current_balance + amount
        cursor.execute('''
            UPDATE user_balance
            SET balance = ?
            WHERE user_id = ?
        ''', (new_balance, target_user_id))

        # Record transaction
        transaction_type = "ADD" if amount > 0 else "REMOVE"
        description = f"Balance {transaction_type} by admin"
        cursor.execute('''
            INSERT INTO transactions (user_id, amount, type, description)
            VALUES (?, ?, ?, ?)
        ''', (target_user_id, amount, transaction_type, description))

        conn.commit()

        # Send log to channel for balance update
        await send_log_to_channel(
            context,
            f"Balance Updated\n"
            f"Admin ID: {user_id}\n"
            f"Target User ID: {target_user_id}\n"
            f"Previous Balance: {current_balance}\n"
            f"Amount: {amount}\n"
            f"New Balance: {new_balance}\n"
            f"Action: {transaction_type}"
        )

        # Send notification to admin
        admin_message = (
            f"✅ Balance updated successfully!\n\n"
            f"User ID: {target_user_id}\n"
            f"Previous Balance: {current_balance}\n"
            f"Amount: {amount}\n"
            f"New Balance: {new_balance}\n"
            f"Action: {transaction_type}\n"
            f"Reason: {description}"
        )
        await update.message.reply_text(admin_message)

        # Send notification to user
        try:
            user_message = (
                f"💰 *Balance Update*\n\n"
                f"Your balance has been updated by admin.\n\n"
                f"Previous Balance: {current_balance}\n"
                f"Amount: {amount}\n"
                f"New Balance: {new_balance}\n"
                f"Action: {transaction_type}\n"
                f"Reason: {description}"
            )
            await context.bot.send_message(
                chat_id=target_user_id,
                text=user_message,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Error sending notification to user {target_user_id}: {str(e)}")
            await update.message.reply_text(f"✅ Balance updated, but couldn't notify user (they might have blocked the bot).")

    except Exception as e:
        logger.error(f"Error in editbalance_command: {str(e)}")
        await update.message.reply_text("An error occurred while updating balance. Please try again.")
    finally:
        conn.close()

async def checkbalance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check another user's balance (Admin only)."""
    try:
        user_id = update.effective_user.id
        if user_id not in ADMIN_USER_IDS:
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return

        if not context.args or len(context.args) != 1:
            await update.message.reply_text("Usage: /checkbalance <user_id>")
            return

        target_user_id = int(context.args[0])
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        cursor.execute('''
            SELECT balance, total_spent, last_updated
            FROM user_balance
            WHERE user_id = ?
        ''', (target_user_id,))

        result = cursor.fetchone()
        if result:
            balance, total_spent, last_updated = result
            balance_text = (
                f"💰 *User Balance*\n\n"
                f"User ID: {target_user_id}\n"
                f"Available Points: {balance}\n"
                f"Total Spent: {total_spent}\n"
                f"Last Updated: {last_updated}"
            )
        else:
            balance_text = f"User {target_user_id} not found in database."

        await update.message.reply_text(balance_text, parse_mode='Markdown')
        logger.info(f"Admin {user_id} checked balance for user {target_user_id}")

    except Exception as e:
        logger.error(f"Error in checkbalance command: {str(e)}")
        await update.message.reply_text("An error occurred while checking balance. Please try again.")
    finally:
        conn.close()

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a broadcast message to all users (Admin only)."""
    try:
        user_id = update.effective_user.id
        if user_id not in ADMIN_USER_IDS:
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return

        if not context.args:
            await update.message.reply_text(
                "Usage: /broadcast <message>\n\n"
                "Example: /broadcast Hello everyone! This is a test message."
            )
            return

        message = ' '.join(context.args)
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        # Get all unique user IDs
        cursor.execute('SELECT DISTINCT user_id FROM user_balance')
        users = cursor.fetchall()

        success_count = 0
        fail_count = 0

        # Send message to each user
        for (user_id,) in users:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📢 *Broadcast Message*\n\n{message}",
                    parse_mode='Markdown'
                )
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to send broadcast to user {user_id}: {str(e)}")
                fail_count += 1

        # Send broadcast summary to admin
        summary = (
            f"📢 *Broadcast Summary*\n\n"
            f"Message sent to {success_count} users\n"
            f"Failed to send to {fail_count} users\n\n"
            f"Message content:\n{message}"
        )

        await update.message.reply_text(summary, parse_mode='Markdown')
        logger.info(f"Admin {user_id} sent broadcast to {success_count} users")

    except Exception as e:
        logger.error(f"Error in broadcast command: {str(e)}")
        await update.message.reply_text("An error occurred while sending broadcast. Please try again.")
    finally:
        conn.close()

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle broadcast message input."""
    try:
        user_id = update.effective_user.id
        if user_id not in ADMIN_USER_IDS:
            return

        state = user_states.get(user_id, {})
        if not state.get('broadcasting'):
            return

        message = update.message.text
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        # Get all unique user IDs
        cursor.execute('SELECT DISTINCT user_id FROM user_balance')
        users = cursor.fetchall()

        success_count = 0
        fail_count = 0

        # Send message to each user
        for (user_id,) in users:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📢 *Broadcast Message*\n\n{message}",
                    parse_mode='Markdown'
                )
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to send broadcast to user {user_id}: {str(e)}")
                fail_count += 1

        # Send broadcast summary to admin
        summary = (
            f"📢 *Broadcast Summary*\n\n"
            f"Message sent to {success_count} users\n"
            f"Failed to send to {fail_count} users\n\n"
            f"Message content:\n{message}"
        )

        await update.message.reply_text(summary, parse_mode='Markdown')
        logger.info(f"Admin {user_id} sent broadcast to {success_count} users")

        # Reset broadcasting state
        user_states[user_id]['broadcasting'] = False

    except Exception as e:
        logger.error(f"Error in handle_broadcast: {str(e)}")
        await update.message.reply_text("An error occurred while sending broadcast. Please try again.")
    finally:
        conn.close()

async def handle_admin_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin balance management commands."""
    try:
        user_id = update.effective_user.id
        if user_id not in ADMIN_USER_IDS:
            return

        state = user_states.get(user_id, {})
        if not any(state.get(key) for key in ['adding_balance', 'removing_balance', 'checking_balance']):
            return

        text = update.message.text.strip()

        if 'adding_balance' in state and state['adding_balance']:
            try:
                target_user_id, amount = map(int, text.split())
                await editbalance_command(update, context, target_user_id, amount, 'add')
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid format. Please use: `user_id amount`\n"
                    "Example: `123456789 100`",
                    parse_mode='Markdown'
                )
            user_states[user_id]['adding_balance'] = False

        elif 'removing_balance' in state and state['removing_balance']:
            try:
                target_user_id, amount = map(int, text.split())
                await editbalance_command(update, context, target_user_id, -amount, 'remove')
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid format. Please use: `user_id amount`\n"
                    "Example: `123456789 50`",
                    parse_mode='Markdown'
                )
            user_states[user_id]['removing_balance'] = False

        elif 'checking_balance' in state and state['checking_balance']:
            try:
                target_user_id = int(text)
                await checkbalance_command(update, context, target_user_id)
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid format. Please send only the user ID.\n"
                    "Example: `123456789`",
                    parse_mode='Markdown'
                )
            user_states[user_id]['checking_balance'] = False

    except Exception as e:
        logger.error(f"Error in handle_admin_balance: {str(e)}")
        await update.message.reply_text("An error occurred. Please try again.")

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new posts from the source channel and automatically index files."""
    try:
        message = update.channel_post or update.message
        if not message or message.chat.id != SOURCE_CHANNEL_ID:
            return

        # Log received message for debugging
        logger.info(f"Received message from channel {message.chat.id}")

        # Check for document/file
        if message.document:
            file = message.document
            file_name = file.file_name
            file_id = file.file_id
            file_size = file.file_size
            mime_type = file.mime_type
            message_id = message.message_id

            # Extract image ID from filename
            try:
                if file_name.startswith('shutterstock_'):
                    image_id = file_name.split('_')[1].split('.')[0]
                else:
                    image_id = file_name
            except:
                image_id = file_name

            # Store in database
            conn = sqlite3.connect('bot_database.db')
            cursor = conn.cursor()

            try:
                # Check if file already exists
                cursor.execute('SELECT id FROM images WHERE file_id = ?', (file_id,))
                existing_file = cursor.fetchone()

                if not existing_file:
                    cursor.execute('''
                        INSERT INTO images
                        (file_id, file_name, image_id, file_size, mime_type, message_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (file_id, file_name, image_id, file_size, mime_type, message_id))

                    conn.commit()

                    # Log successful indexing
                    log_message = (
                        f"✅ New File Auto-Indexed\n"
                        f"File Name: {file_name}\n"
                        f"Image ID: {image_id}\n"
                        f"Size: {file_size} bytes\n"
                        f"Type: {mime_type}\n"
                        f"Message ID: {message_id}"
                    )

                    await send_log_to_channel(context, log_message)
                    logger.info(f"Successfully auto-indexed new file: {file_name}")

            except Exception as e:
                error_msg = f"Error saving file to database: {str(e)}"
                logger.error(error_msg)
                await send_log_to_channel(
                    context,
                    f"❌ Error Auto-Indexing File\n"
                    f"File Name: {file_name}\n"
                    f"Error: {str(e)}"
                )
            finally:
                conn.close()

    except Exception as e:
        logger.error(f"Error in handle_channel_post: {str(e)}")
        await send_log_to_channel(context, f"❌ Channel Handler Error: {str(e)}")

class ChannelFilter(filters.MessageFilter):
    """Custom filter for channel messages."""
    def filter(self, message):
        return (message.chat.type == Chat.CHANNEL and
                message.chat.id == SOURCE_CHANNEL_ID)

def main():
    """Start the bot."""
    try:
        # Create the Application
        application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

        # Add channel handler FIRST
        application.add_handler(MessageHandler(
            filters.ChatType.CHANNEL & filters.Document.ALL,
            handle_channel_post
        ))

        # Add command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("balance", balance_command))
        application.add_handler(CommandHandler("addbalance", addbalance_command))
        application.add_handler(CommandHandler("editbalance", editbalance_command))
        application.add_handler(CommandHandler("checkbalance", checkbalance_command))
        application.add_handler(CommandHandler("index", index_command))
        application.add_handler(CommandHandler("indexdone", index_done_command))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("search", search_command))
        application.add_handler(CommandHandler("broadcast", broadcast_command))

        # Add callback query handler (for inline buttons) BEFORE message handlers
        application.add_handler(CallbackQueryHandler(button_handler))

        # Add message handlers
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_image_id))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_balance))
        application.add_handler(MessageHandler(filters.Document.ALL, handle_file))

        # Add error handler
        application.add_error_handler(error_handler)

        # Start the Bot
        logger.info(f"Starting bot... Monitoring channel: {SOURCE_CHANNEL_ID}")
        application.run_polling(allowed_updates=["message", "channel_post", "callback_query"])

    except Exception as e:
        logger.error(f"Failed to start bot: {str(e)}")
        raise

if __name__ == '__main__':
    main()
