#!/usr/bin/env python3
"""
Social Media Downloader Bot - Discord Version (Render Ready)
Author: @UnknownGuy9876
Original Telegram Bot converted to Discord
"""

import os
import sys
import json
import logging
import time
import sqlite3
import threading
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# Rich for console output
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Discord
import discord
from discord.ext import commands
from discord import app_commands

# Downloader
from yt_dlp import YoutubeDL

# Initialize
console = Console()
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============ CONFIGURATION ============
class Config:
    # Get token from environment variable
    DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
    if not DISCORD_TOKEN:
        console.print("[red]❌ DISCORD_TOKEN environment variable not set![/red]")
        console.print("[yellow]Please set it in Render dashboard:[/yellow]")
        console.print("1. Go to your service on Render")
        console.print("2. Click 'Environment' tab")
        console.print("3. Add 'DISCORD_TOKEN' variable with your bot token")
        sys.exit(1)
    
    # Admin IDs (optional - set in environment as comma-separated list)
    ADMIN_IDS = []
    admin_ids_str = os.environ.get('ADMIN_IDS', '')
    if admin_ids_str:
        ADMIN_IDS = [int(id.strip()) for id in admin_ids_str.split(',') if id.strip()]
    
    # Server invite link (optional)
    CHANNEL_LINK = os.environ.get('SERVER_INVITE', 'https://discord.gg/yourserver')
    
    # Bot username
    BOT_USERNAME = os.environ.get('BOT_USERNAME', 'Social Media Downloader')
    
    # Storage limits
    MAX_STORAGE_MB = int(os.environ.get('MAX_STORAGE_MB', '1000'))
    AUTO_CLEANUP_HOURS = int(os.environ.get('AUTO_CLEANUP_HOURS', '1'))
    MAX_FILE_SIZE_MB = int(os.environ.get('MAX_FILE_SIZE_MB', '25'))  # Discord limit is 25MB
    
    # User limits
    MAX_DOWNLOADS_PER_DAY = int(os.environ.get('MAX_DOWNLOADS_PER_DAY', '50'))
    RATE_LIMIT_PER_HOUR = int(os.environ.get('RATE_LIMIT_PER_HOUR', '30'))
    
    # Paths (use /tmp for Render's ephemeral storage)
    BASE_DIR = '/tmp' if os.environ.get('RENDER', False) else '.'
    DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')
    TEMP_DIR = os.path.join(BASE_DIR, 'temp')
    DB_PATH = os.path.join(BASE_DIR, 'downloads.db')
    
    # yt-dlp settings
    YDL_OPTIONS = {
        'quiet': True,
        'no_warnings': False,
        'ignoreerrors': False,
        'no_color': True,
        
        # Fix for YouTube bot detection
        'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
                'player_skip': ['configs', 'webpage'],
            }
        },
        
        # Headers to mimic browser
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        },
        
        # Retry settings
        'retries': 10,
        'fragment_retries': 10,
        'skip_unavailable_fragments': True,
        'retry_sleep_functions': {
            'http': lambda n: 3,
            'fragment': lambda n: 3,
            'file_access': lambda n: 3,
        },
        
        # Network settings
        'socket_timeout': 30,
        'extract_timeout': 180,
    }
    
    # Remove None values from YDL_OPTIONS
    YDL_OPTIONS = {k: v for k, v in YDL_OPTIONS.items() if v is not None}

# ============ STORAGE MANAGER ============
class StorageManager:
    def __init__(self):
        self.download_dir = Config.DOWNLOAD_DIR
        self.temp_dir = Config.TEMP_DIR
        self.db_path = Config.DB_PATH
        
        # Create directories
        os.makedirs(self.download_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        
        # Initialize database
        self.init_database()
        
        # Start cleanup scheduler
        self.start_cleanup_scheduler()
        console.print("[green]✓ Storage Manager initialized[/green]")
        console.print(f"[cyan]Storage path: {self.download_dir}[/cyan]")
    
    def init_database(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_name TEXT,
                platform TEXT,
                url TEXT,
                filename TEXT,
                file_path TEXT,
                file_size INTEGER,
                status TEXT DEFAULT 'pending',
                download_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent_time TIMESTAMP,
                deleted BOOLEAN DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT,
                total_downloads INTEGER DEFAULT 0,
                downloads_today INTEGER DEFAULT 0,
                last_download_date DATE,
                joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def log_download(self, user_id: int, user_name: str, platform: str, 
                    url: str, filename: str, file_path: str) -> int:
        """Log a new download in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        
        cursor.execute('''
            INSERT INTO downloads 
            (user_id, user_name, platform, url, filename, file_path, file_size, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'downloaded')
        ''', (user_id, user_name, platform, url, filename, file_path, file_size))
        
        download_id = cursor.lastrowid
        
        # Update user stats
        today = datetime.now().date()
        cursor.execute('''
            INSERT OR IGNORE INTO user_stats (user_id, user_name, joined_date)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, user_name))
        
        cursor.execute('''
            UPDATE user_stats 
            SET total_downloads = total_downloads + 1,
                downloads_today = CASE 
                    WHEN last_download_date = DATE(?) THEN downloads_today + 1 
                    ELSE 1 
                END,
                last_download_date = DATE(?)
            WHERE user_id = ?
        ''', (today, today, user_id))
        
        conn.commit()
        conn.close()
        
        return download_id
    
    def mark_as_sent(self, download_id: int):
        """Mark download as sent to user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE downloads 
            SET status = 'sent', 
                sent_time = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (download_id,))
        
        conn.commit()
        conn.close()
    
    def cleanup_old_files(self, hours_old: int = None):
        """Clean up files older than specified hours"""
        if hours_old is None:
            hours_old = Config.AUTO_CLEANUP_HOURS
        
        cutoff_time = datetime.now() - timedelta(hours=hours_old)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, file_path FROM downloads 
            WHERE download_time < ? AND deleted = 0
        ''', (cutoff_time,))
        
        deleted_count = 0
        for file_id, file_path in cursor.fetchall():
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                cursor.execute('UPDATE downloads SET deleted = 1 WHERE id = ?', (file_id,))
                deleted_count += 1
            except:
                pass
        
        conn.commit()
        conn.close()
        
        # Clean empty directories
        self.clean_empty_dirs()
        
        if deleted_count > 0:
            console.print(f"[cyan]🧹 Cleaned {deleted_count} old files[/cyan]")
        
        return deleted_count
    
    def clean_empty_dirs(self):
        """Remove empty directories"""
        for dirpath, dirnames, filenames in os.walk(self.download_dir, topdown=False):
            for dirname in dirnames:
                full_path = os.path.join(dirpath, dirname)
                try:
                    if not os.listdir(full_path):
                        os.rmdir(full_path)
                except:
                    pass
    
    def get_user_stats(self, user_id: int) -> Dict:
        """Get user download statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT total_downloads, downloads_today, joined_date
            FROM user_stats 
            WHERE user_id = ?
        ''', (user_id,))
        
        result = cursor.fetchone()
        
        if result:
            total_downloads, downloads_today, joined_date = result
        else:
            total_downloads = downloads_today = 0
            joined_date = datetime.now()
        
        conn.close()
        
        return {
            'total_downloads': total_downloads,
            'downloads_today': downloads_today,
            'max_per_day': Config.MAX_DOWNLOADS_PER_DAY,
            'joined_date': joined_date,
            'remaining_today': max(0, Config.MAX_DOWNLOADS_PER_DAY - downloads_today)
        }
    
    def can_user_download(self, user_id: int) -> tuple[bool, str]:
        """Check if user can download"""
        stats = self.get_user_stats(user_id)
        
        if stats['downloads_today'] >= Config.MAX_DOWNLOADS_PER_DAY:
            return False, f"You've reached your daily limit ({Config.MAX_DOWNLOADS_PER_DAY} downloads). Try again tomorrow!"
        
        return True, ""
    
    def start_cleanup_scheduler(self):
        """Start automatic cleanup scheduler"""
        def cleanup_job():
            self.cleanup_old_files()
        
        # Use threading for scheduler
        import schedule
        
        schedule.every(30).minutes.do(cleanup_job)
        
        def run_scheduler():
            while True:
                schedule.run_pending()
                time.sleep(60)
        
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()

# ============ DOWNLOAD MANAGER ============
class DownloadManager:
    def __init__(self):
        self.user_sessions: Dict[int, Dict] = {}
        self.platforms = [
            "YouTube", "Instagram", "TikTok", "Twitter/X",
            "Facebook", "Reddit", "LinkedIn", "Pinterest",
            "Vimeo", "Dailymotion", "SoundCloud", "Twitch",
            "Snapchat", "Likee", "Bilibili"
        ]
        
        # Test YouTube connection on startup
        self.test_youtube_connection()
    
    def test_youtube_connection(self):
        """Test YouTube connection on startup"""
        console.print("[cyan]Testing YouTube connection...[/cyan]")
        test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        
        try:
            test_opts = Config.YDL_OPTIONS.copy()
            test_opts['quiet'] = True
            test_opts['extract_flat'] = True
            
            with YoutubeDL(test_opts) as ydl:
                info = ydl.extract_info(test_url, download=False)
                if info:
                    console.print("[green]✓ YouTube connection successful![/green]")
                else:
                    console.print("[yellow]⚠ YouTube test failed (no info)[/yellow]")
        except Exception as e:
            console.print(f"[red]✗ YouTube test failed: {str(e)[:100]}[/red]")
            console.print("[yellow]You may need to add cookies.txt file[/yellow]")
    
    def detect_platform(self, url: str) -> str:
        """Detect social media platform from URL"""
        url_lower = url.lower()
        
        if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
            return 'YouTube'
        elif 'instagram.com' in url_lower:
            return 'Instagram'
        elif 'tiktok.com' in url_lower:
            return 'TikTok'
        elif 'twitter.com' in url_lower or 'x.com' in url_lower:
            return 'Twitter/X'
        elif 'facebook.com' in url_lower or 'fb.watch' in url_lower:
            return 'Facebook'
        elif 'reddit.com' in url_lower:
            return 'Reddit'
        else:
            return 'Unknown'
    
    def get_ydl_options(self, format_choice: str, platform: str) -> Dict:
        """Get yt-dlp options"""
        # Create timestamp for unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"%(title)s_{timestamp}.%(ext)s"
        output_path = os.path.join(storage_manager.download_dir, filename)
        
        ydl_opts = Config.YDL_OPTIONS.copy()
        ydl_opts['outtmpl'] = output_path
        
        # Platform-specific settings
        if platform == "YouTube":
            ydl_opts.update({
                'format': self.get_youtube_format(format_choice),
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android', 'web'],
                        'player_skip': ['configs', 'webpage'],
                    }
                }
            })
        elif platform == "Instagram":
            ydl_opts['format'] = 'best'
            ydl_opts['extractor_args'] = {'instagram': {'post': 'single'}}
        elif platform == "TikTok":
            ydl_opts['format'] = 'best'
            ydl_opts['extractor_args'] = {'tiktok': {'app_version': '29.7.4'}}
        else:
            ydl_opts['format'] = 'best'
        
        # Format selection
        format_map = {
            "video": "bv*+ba/b",
            "audio": "ba",
            "medium": "best[height<=720]/best",
            "small": "best[height<=480]/best",
        }
        
        if format_choice in format_map:
            ydl_opts['format'] = format_map[format_choice]
        
        # Audio extraction
        if format_choice == "audio":
            ydl_opts['format'] = 'bestaudio'
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        
        return ydl_opts
    
    def get_youtube_format(self, format_choice: str) -> str:
        """Get YouTube format string"""
        format_map = {
            "video": "bv*+ba/b",
            "audio": "ba",
            "medium": "best[height<=720]/best",
            "small": "best[height<=480]/best",
        }
        return format_map.get(format_choice, "bv*+ba/b")
    
    def progress_hook(self, d):
        """Progress hook for downloads"""
        if d['status'] == 'downloading':
            percent = d.get('_percent_str', '0%').strip()
            speed = d.get('_speed_str', 'N/A').strip()
            console.print(f"[cyan]Progress: {percent} | Speed: {speed}[/cyan]", end="\r")
        elif d['status'] == 'finished':
            console.print("\n[green]✓ Download completed[/green]")

# ============ DISCORD BOT ============
class SocialMediaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        
        super().__init__(command_prefix='!', intents=intents)
        
        # Remove default help command
        self.remove_command('help')
    
    async def setup_hook(self):
        """Setup hook for syncing commands"""
        await self.tree.sync()
        console.print("[green]✓ Slash commands synced[/green]")

# Initialize bot
bot = SocialMediaBot()
storage_manager = StorageManager()
download_manager = DownloadManager()

# ============ DISCORD EVENTS ============
@bot.event
async def on_ready():
    """Called when bot is ready"""
    console.print(f"[green]✓ Logged in as {bot.user.name}[/green]")
    console.print(f"[green]✓ Bot ID: {bot.user.id}[/green]")
    
    # Set activity
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="/help | Social Media Downloader"
        )
    )
    
    # Display banner
    banner = Panel.fit(
        f"[bold cyan]Social Media Downloader Bot - Discord Version[/bold cyan]\n"
        f"[green]Bot: {bot.user.name}[/green]\n"
        f"[yellow]Server Count: {len(bot.guilds)}[/yellow]\n\n"
        f"[cyan]Storage: {Config.BASE_DIR}[/cyan]\n"
        f"[cyan]YouTube Fix Applied![/cyan]\n"
        f"✅ Storage: Active cleanup\n"
        f"✅ Database: Ready\n"
        f"✅ Commands: /start, /help, /stats",
        title="🤖 Bot Status - DISCORD (Render)",
        border_style="cyan"
    )
    
    console.print(banner)

@bot.event
async def on_message(message):
    """Handle messages with URLs"""
    if message.author.bot:
        return
    
    # Check if message contains a URL
    if message.content.startswith(('http://', 'https://', 'www.')):
        ctx = await bot.get_context(message)
        await handle_url(ctx, message.content)
    
    await bot.process_commands(message)

# ============ DISCORD COMMANDS ============
@bot.tree.command(name="start", description="Show welcome message")
async def start(interaction: discord.Interaction):
    """Send welcome message"""
    embed = discord.Embed(
        title=f"👋 Welcome {interaction.user.name}!",
        description="""
🎬 **Social Media Downloader Bot**
Download videos from 15+ platforms instantly!

**Supported Platforms:**
• YouTube (Videos/Shorts)
• Instagram (Reels/Posts)
• TikTok (Without Watermark)
• Twitter/X (Videos)
• Facebook (Videos)
• Reddit, and more!

**How to use:**
1. Send any social media link
2. Choose format quality
3. Get your video/audio!

**Commands:**
`/start` - Show this message
`/stats` - Your download statistics
`/help` - Help & instructions
        """,
        color=discord.Color.blue()
    )
    
    embed.set_footer(text=f"Bot by {Config.BOT_USERNAME}")
    
    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="Join Server",
        url=Config.CHANNEL_LINK,
        style=discord.ButtonStyle.link
    ))
    
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="help", description="Show help instructions")
async def help_command(interaction: discord.Interaction):
    """Send help instructions"""
    embed = discord.Embed(
        title="📚 How to Download",
        description="""
**1.** Copy any social media video link
**2.** Send it in this channel or DM the bot
**3.** Choose your preferred quality
**4.** Wait for download (10-30 seconds)
**5.** Receive your video/audio!

**Tips for YouTube:**
• If you get "Sign in" error, use Medium or Small quality
• Some videos might require cookies (contact admin)
• Audio Only option works best for restricted videos

**Supported Sites:**
• YouTube, Instagram, TikTok
• Twitter/X, Facebook, Reddit
• And 10+ more platforms!

**File Size Limit:** 25MB (Discord limit)
        """,
        color=discord.Color.green()
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="stats", description="Show your download statistics")
async def stats_command(interaction: discord.Interaction):
    """Show user statistics"""
    user_stats = storage_manager.get_user_stats(interaction.user.id)
    
    embed = discord.Embed(
        title="📊 Your Statistics",
        color=discord.Color.purple()
    )
    
    embed.add_field(name="👤 User", value=f"{interaction.user.mention}", inline=True)
    embed.add_field(name="🆔 ID", value=f"`{interaction.user.id}`", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    
    embed.add_field(name="📥 Today", value=f"{user_stats['downloads_today']}/{user_stats['max_per_day']}", inline=True)
    embed.add_field(name="📈 Total", value=f"{user_stats['total_downloads']}", inline=True)
    embed.add_field(name="✨ Remaining", value=f"{user_stats['remaining_today']}", inline=True)
    
    embed.set_footer(text=f"Joined: {user_stats['joined_date'].strftime('%Y-%m-%d')}")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="download", description="Download from a URL")
@app_commands.describe(url="The URL to download from")
async def download_command(interaction: discord.Interaction, url: str):
    """Download from a URL using slash command"""
    await handle_url(interaction, url)

async def handle_url(ctx, url: str):
    """Handle URL processing"""
    user = ctx.author if hasattr(ctx, 'author') else ctx.user
    
    # Check if it's a valid URL
    if not url.startswith(('http://', 'https://')):
        embed = discord.Embed(
            title="❌ Invalid URL",
            description="Please send a valid URL starting with `http://` or `https://`",
            color=discord.Color.red()
        )
        
        if hasattr(ctx, 'response'):
            await ctx.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.reply(embed=embed)
        return
    
    # Check rate limit
    can_download, reason = storage_manager.can_user_download(user.id)
    if not can_download:
        embed = discord.Embed(
            title="⚠️ Daily Limit Reached",
            description=reason,
            color=discord.Color.orange()
        )
        
        if hasattr(ctx, 'response'):
            await ctx.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.reply(embed=embed)
        return
    
    # Detect platform
    platform = download_manager.detect_platform(url)
    
    # Create selection menu
    view = FormatSelectionView(user.id, url, platform)
    
    embed = discord.Embed(
        title=f"✅ {platform} Link Detected!",
        description="Please select the format you want to download:",
        color=discord.Color.blue()
    )
    
    embed.add_field(name="URL", value=f"`{url[:50]}...`" if len(url) > 50 else f"`{url}`", inline=False)
    
    if hasattr(ctx, 'response'):
        await ctx.response.send_message(embed=embed, view=view, ephemeral=True)
    else:
        await ctx.reply(embed=embed, view=view)

# ============ DISCORD UI COMPONENTS ============
class FormatSelectionView(discord.ui.View):
    def __init__(self, user_id: int, url: str, platform: str):
        super().__init__(timeout=300)  # 5 minute timeout
        self.user_id = user_id
        self.url = url
        self.platform = platform
    
    @discord.ui.button(label="🎬 Video (Best)", style=discord.ButtonStyle.primary, custom_id="video")
    async def video_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This menu is not for you!", ephemeral=True)
            return
        await self.process_download(interaction, "video")
    
    @discord.ui.button(label="🎵 Audio Only", style=discord.ButtonStyle.secondary, custom_id="audio")
    async def audio_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This menu is not for you!", ephemeral=True)
            return
        await self.process_download(interaction, "audio")
    
    @discord.ui.button(label="📱 Medium (720p)", style=discord.ButtonStyle.success, custom_id="medium")
    async def medium_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This menu is not for you!", ephemeral=True)
            return
        await self.process_download(interaction, "medium")
    
    @discord.ui.button(label="💾 Small (480p)", style=discord.ButtonStyle.success, custom_id="small")
    async def small_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This menu is not for you!", ephemeral=True)
            return
        await self.process_download(interaction, "small")
    
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger, custom_id="cancel")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This menu is not for you!", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="❌ Download Cancelled",
            color=discord.Color.red()
        )
        
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()
    
    async def process_download(self, interaction: discord.Interaction, format_choice: str):
        """Process the download"""
        await interaction.response.defer()
        
        # Update message
        embed = discord.Embed(
            title=f"⏬ Downloading from {self.platform}...",
            description="⏳ Please wait, this may take 10-30 seconds...",
            color=discord.Color.gold()
        )
        
        await interaction.edit_original_response(embed=embed, view=None)
        
        try:
            # Get download options
            ydl_opts = download_manager.get_ydl_options(format_choice, self.platform)
            ydl_opts['progress_hooks'] = [download_manager.progress_hook]
            
            console.print(f"\n{'='*50}")
            console.print(f"[cyan]Starting download...[/cyan]")
            console.print(f"[yellow]Platform: {self.platform}[/yellow]")
            console.print(f"[yellow]URL: {self.url}[/yellow]")
            console.print(f"[yellow]Format: {format_choice}[/yellow]")
            
            # Handle YouTube specially
            if self.platform == "YouTube":
                await self.handle_youtube_download(interaction, ydl_opts, format_choice)
            else:
                await self.handle_other_platforms(interaction, ydl_opts)
                
        except Exception as e:
            await self.show_error(interaction, str(e))
    
    async def handle_youtube_download(self, interaction: discord.Interaction, ydl_opts: Dict, format_choice: str):
        """Handle YouTube downloads with fallback methods"""
        url = self.url
        platform = self.platform
        user = interaction.user
        
        # Method 1: Standard
        try:
            console.print("[cyan]Trying Method 1: Standard download...[/cyan]")
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                await self.send_file(interaction, info, url, user.id, user.name, platform, ydl)
            return
        except Exception as e1:
            console.print(f"[yellow]Method 1 failed: {str(e1)[:100]}[/yellow]")
        
        # Method 2: Simpler format
        try:
            embed = discord.Embed(
                title="🔄 Method 1 Failed",
                description="Trying alternative format...",
                color=discord.Color.orange()
            )
            await interaction.edit_original_response(embed=embed)
            
            alt_opts = ydl_opts.copy()
            if format_choice == "video":
                alt_opts['format'] = 'best[height<=720]'
            elif format_choice == "medium":
                alt_opts['format'] = 'best[height<=480]'
            elif format_choice == "small":
                alt_opts['format'] = 'worst'
            
            console.print("[cyan]Trying Method 2: Simpler format...[/cyan]")
            with YoutubeDL(alt_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                await self.send_file(interaction, info, url, user.id, user.name, platform, ydl)
            return
        except Exception as e2:
            console.print(f"[yellow]Method 2 failed: {str(e2)[:100]}[/yellow]")
        
        # Method 3: Audio only
        try:
            embed = discord.Embed(
                title="🔄 Trying Audio Only...",
                description="Video download failed, trying audio extraction",
                color=discord.Color.orange()
            )
            await interaction.edit_original_response(embed=embed)
            
            audio_opts = ydl_opts.copy()
            audio_opts['format'] = 'bestaudio'
            audio_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
            
            console.print("[cyan]Trying Method 3: Audio only...[/cyan]")
            with YoutubeDL(audio_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                await self.send_file(interaction, info, url, user.id, user.name, platform, ydl)
            return
        except Exception as e3:
            console.print(f"[red]All methods failed: {str(e3)[:100]}[/red]")
            raise Exception(f"All download methods failed. Last error: {str(e3)}")
    
    async def handle_other_platforms(self, interaction: discord.Interaction, ydl_opts: Dict):
        """Handle non-YouTube downloads"""
        url = self.url
        platform = self.platform
        user = interaction.user
        
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            await self.send_file(interaction, info, url, user.id, user.name, platform, ydl)
    
    async def send_file(self, interaction: discord.Interaction, info: Dict, url: str, 
                       user_id: int, user_name: str, platform: str, ydl: YoutubeDL):
        """Send the downloaded file"""
        # Get downloaded file
        file_path = ydl.prepare_filename(info)
        
        # Check if file exists
        if not os.path.exists(file_path):
            base_name = os.path.splitext(file_path)[0]
            for ext in ['.mp4', '.mkv', '.webm', '.mp3', '.m4a', '.m4v']:
                test_path = base_name + ext
                if os.path.exists(test_path):
                    file_path = test_path
                    break
        
        if not os.path.exists(file_path):
            raise Exception("Downloaded file not found")
        
        # Check file size
        file_size = os.path.getsize(file_path)
        if file_size > Config.MAX_FILE_SIZE_MB * 1024 * 1024:
            os.remove(file_path)
            raise Exception(f"File too large ({file_size//1024//1024}MB). Discord limit is {Config.MAX_FILE_SIZE_MB}MB. Try lower quality.")
        
        # Log download
        download_id = storage_manager.log_download(
            user_id=user_id,
            user_name=user_name,
            platform=platform,
            url=url,
            filename=os.path.basename(file_path),
            file_path=file_path
        )
        
        # Update status
        video_title = info.get('title', 'Video')[:100]
        
        embed = discord.Embed(
            title="✅ Download Complete!",
            description=f"📹 {video_title}\n📏 Size: {file_size//1024//1024}MB\n📤 Sending to you...",
            color=discord.Color.green()
        )
        
        await interaction.edit_original_response(embed=embed)
        
        # Send file
        try:
            with open(file_path, 'rb') as f:
                discord_file = discord.File(f, filename=os.path.basename(file_path))
                
                embed = discord.Embed(
                    title=f"✅ Downloaded from {platform}",
                    description=f"📹 {video_title}\n\n[Bot by {Config.BOT_USERNAME}]({Config.CHANNEL_LINK})",
                    color=discord.Color.blue()
                )
                
                if file_path.endswith(('.mp3', '.m4a', '.ogg', '.wav')):
                    await interaction.followup.send(embed=embed, file=discord_file)
                else:
                    await interaction.followup.send(embed=embed, file=discord_file)
            
            # Mark as sent
            storage_manager.mark_as_sent(download_id)
            
            # Delete file
            os.remove(file_path)
            console.print(f"[green]✓ File sent and deleted[/green]")
            
        except Exception as e:
            console.print(f"[red]Error sending file: {e}[/red]")
            raise
        
        # Final success message
        success_embed = discord.Embed(
            title="🎉 Successfully Sent!",
            description=f"✨ Thank you for using our service!\n\n**Bot by:** {Config.BOT_USERNAME}\n[Join our server]({Config.CHANNEL_LINK})",
            color=discord.Color.gold()
        )
        
        await interaction.followup.send(embed=success_embed)
    
    async def show_error(self, interaction: discord.Interaction, error_msg: str):
        """Show error message"""
        # Clean error message
        if "Sign in" in error_msg:
            error_msg = "YouTube requires authentication. Try Medium or Small quality."
        elif "Requested format is not available" in error_msg:
            error_msg = "Format not available. Try different quality."
        elif "Unavailable" in error_msg:
            error_msg = "Video not available or restricted."
        elif "Private" in error_msg:
            error_msg = "Video is private."
        elif "too large" in error_msg:
            error_msg = f"File too large for Discord ({Config.MAX_FILE_SIZE_MB}MB limit). Try lower quality."
        
        embed = discord.Embed(
            title=f"❌ Download Failed - {self.platform}",
            description=f"**Error:** {error_msg[:200]}\n\n**Try these solutions:**\n• Use Medium or Small quality\n• Try Audio Only option\n• Try a different video\n• Wait a few minutes",
            color=discord.Color.red()
        )
        
        embed.set_footer(text="Need help? Contact server admin")
        
        await interaction.edit_original_response(embed=embed, view=None)

# ============ MAIN FUNCTION ============
def main():
    """Start the bot."""
    # Display startup banner
    banner = Panel.fit(
        f"[bold cyan]Social Media Downloader Bot - Discord Version[/bold cyan]\n"
        f"[green]Starting on Render...[/green]\n\n"
        f"[cyan]Environment:[/cyan]\n"
        f"• DISCORD_TOKEN: {'✅ Set' if Config.DISCORD_TOKEN else '❌ Missing'}\n"
        f"• Storage Path: {Config.BASE_DIR}\n"
        f"• Max File Size: {Config.MAX_FILE_SIZE_MB}MB\n"
        f"• Daily Limit: {Config.MAX_DOWNLOADS_PER_DAY}\n"
        f"• Cleanup: {Config.AUTO_CLEANUP_HOURS}h",
        title="🤖 Bot Status",
        border_style="cyan"
    )
    
    console.print(banner)
    
    # Check for cookies.txt
    if os.path.exists('cookies.txt'):
        console.print("[green]✓ cookies.txt found[/green]")
    else:
        console.print("[yellow]⚠ No cookies.txt found[/yellow]")
        console.print("[cyan]YouTube downloads might be limited[/cyan]")
    
    # Run bot
    try:
        bot.run(Config.DISCORD_TOKEN)
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot stopped by user.[/yellow]")
    except Exception as e:
        console.print(f"[red]Fatal error: {e}[/red]")
        sys.exit(1)

if __name__ == '__main__':
    main()