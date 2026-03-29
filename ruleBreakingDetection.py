import requests
from dotenv import load_dotenv
import os
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import json
from datetime import datetime, timedelta
import discord
from discord.ext import commands, tasks
from discord import app_commands
import re
import logging
import webserver


logging.basicConfig(level=logging.INFO)  # or DEBUG for more detail
logger = logging.getLogger(__name__)

num_map = {
    "1": "first",
    "2": "second",
    "3": "third",
    "4": "fourth"
}
MAP_FILE="map.json"
SETTINGS_FILE ="settings.json"
node_names = {
    "6": "second node",
    "11": "last node",
}
STATS_URL = "https://demonicscans.org/guild_dungeon_cube_army_action.php"
FIRST_NODE = "5"
FIRST_NODE_MATCH_NO= 4

if os.path.exists(".env"):
    load_dotenv()


try:
    with open(MAP_FILE, "r") as f:
        dungeon_map = json.load(f)
except FileNotFoundError:
    dungeon_map = {}

try:
    with open(SETTINGS_FILE, "r") as f:
        nodes = json.load(f)
except FileNotFoundError:
    nodes = {
        "6": "12:00:00",
        "11": "18:00:00"
    }

actual_node_times={}
TOKEN = os.environ["BOT_TOKEN"]
GUILD_ID  = None
CHANNEL_ID = None


CUBE = "The Polyhedral Crucible"
HARD = "Castle of the Fallen Prince"
NORMAL = "Shadowbridge Warrens"

match_number = {
    "6":4,
    "11":1
}
cookie_dict=json.loads(os.environ["COOKIES"])
loop_time = int(os.environ["LOOP_TIME"]) or 10
GUILD_ID = int(os.environ["GUILD_ID"])  # your server ID
guild = discord.Object(id=GUILD_ID)

warnings = {}


def getDungeonData(dungeon,soup):
    # Find first div.card that contains the text "The Polyhedral Crucible"
    card = None
    opened_date = None

    for div in soup.find_all("div", class_="card"):
        header = div.find("div", class_="h")
        if header and dungeon in header.text:
            card = div
            break

    if card is None:
        return -1    
    enter_btn = card.find("a", string="Enter")
    
    if enter_btn is None:
        return -1    
    href = enter_btn["href"]
    parsed = urlparse(href)
    id_value = parse_qs(parsed.query)["id"][0]

    span_warn = card.find("span", class_="tag warn")
    if span_warn:
        text = span_warn.get_text(strip=True)  # e.g., "Opened today @ 2026-03-29 00:15:21"
        # Extract the datetime after the '@'
        if "@" in text:
            opened_date = text.split("@")[1].strip()  # "2026-03-29 00:15:21"

    return id_value,opened_date


def queue_warning(attacker, node, match_no, threshold_time_str):
    message = (
        f"⚠️ You attacked the __**{node_names[node]}**__ {num_map[match_no]} Army "
        f"before __**{threshold_time_str}**__ and dealt {attacker.get('damage_dealt')} damage "
        f"at  __**{attacker.get('last_action_at')}**__"
    )
    warnings[attacker.get("username")] = message

def parse_time(ts):
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")

def updateMap(name,dmg,last_action,attacker_map):
    new_time = parse_time(last_action)
    if dmg == 0:
        return False
    if name not in attacker_map:
        attacker_map[name] = {"damage": dmg, "last_action": last_action}
        return True
    else:
        old = attacker_map[name]
        old_dmg = old["damage"]
        old_time = parse_time(old["last_action"])
        if dmg > old_dmg and new_time > old_time:
            attacker_map[name] = {"damage": dmg, "last_action": last_action}
            return True
    return False



def getInvalidAttacks(threshold_date_str,node_id,node_map,cube_id):
    max_match_no = match_number[node_id]
    for match_no in map(str, range(1, max_match_no + 1)):
        attackers = getAttackers(node_id, cube_id, match_no)
        if match_no in node_map:
            match_map=node_map[match_no]
        else:
            match_map={}

        for attacker in attackers:
            last_action_date_str = attacker.get("last_action_at")  # e.g., "2026-03-27 07:26:58"
            
            if last_action_date_str < threshold_date_str:
                if updateMap(attacker.get("username"),attacker.get("damage_dealt"),last_action_date_str,match_map):
                    queue_warning(attacker,node_id,match_no,threshold_date_str)

        node_map[match_no]=match_map
    
    return node_map
def combine_date_and_time(date_str, time_str):
    # Parse the original date
    date_dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    # Parse the new time
    new_time_dt = datetime.strptime(time_str, "%H:%M:%S").time()
    
    # Combine date with new time
    combined = datetime.combine(date_dt.date(), new_time_dt)
    
    # If the original time is later than the new time, move to next day
    if date_dt.time() > new_time_dt:
        combined += timedelta(days=1)
    
    return combined.strftime("%Y-%m-%d %H:%M:%S")

def getAttackers(node_id, cube_id, match_no):
    data = {
            "action": "contributors",
            "instance_id": cube_id,
            "node_id": node_id,
            "match_no":match_no
        }
    res = requests.post(STATS_URL,data=data, cookies=cookie_dict)



    json_data =res.text  # your full JSON here

    data = json.loads(json_data)
    if not data or not data.get("board"):
        attackers=[]
    else:
        attackers=data.get("board").get("attackers", [])
    return attackers

    


def normalize_name(name: str) -> str:
    # remove [demon] (any case)
    name = re.sub(r"\[demon\]", "", name, flags=re.IGNORECASE)
    
    # normalize spaces
    name = " ".join(name.split())
    
    return name.lower()  # optional for comparison

def getGameTime():
    utc_now = datetime.utcnow()

    # Game server offset (UTC +5:30)
    game_offset = timedelta(hours=5, minutes=30)

    # Current game time
    game_time_now = utc_now + game_offset

    # Format as YYYY-MM-DD HH:MM:SS
    game_time_str = game_time_now.strftime("%Y-%m-%d %H:%M:%S")

    logger.info(f"Game time now : {game_time_str}")
    return game_time_str

def getFirstShadowArmyAttack(cube_id):
    attackDates = []
    for match_no in map(str, range(1, FIRST_NODE_MATCH_NO + 1)):
        attackDates.extend([player["joined_at"] for player in getAttackers(FIRST_NODE,cube_id,match_no)])
    min_date = min(attackDates) if attackDates else getGameTime()
    return min_date


async def run_task():
    global dungeon_map
    global cookie_dict
    url = "https://demonicscans.org/guild_dungeon.php"
    res = requests.get(url, cookies=cookie_dict)
    soup = BeautifulSoup(res.text, "html.parser")
    cube_id,open_date = getDungeonData(CUBE,soup)
    if cube_id == -1:
        return
    if "dungeon_id" not in dungeon_map or  dungeon_map["dungeon_id"] != cube_id: #new dungeon opened
        dungeon_map={}
        set_dungeon_open_date(cube_id, open_date)
    if "open_date" not in dungeon_map:
        set_dungeon_open_date(cube_id, open_date)

    
    open_date = dungeon_map["open_date"]
    logger.info(f"open_date : {open_date}")
    getGameTime()
    for node in nodes:
        actual_node_times[node]=combine_date_and_time(open_date,nodes[node])
    logger.info(f"node-times : {actual_node_times}")
    result_map ={}
    result_map["dungeon_id"]=cube_id
    result_map["open_date"]=open_date
    for node_id in nodes:
        if node_id in dungeon_map:
            node_map = dungeon_map[node_id]
        else:
            node_map={}
        threshold_date_str = actual_node_times[node_id]
        node_map = getInvalidAttacks(threshold_date_str,node_id,node_map,cube_id)
        result_map[node_id] = node_map
    dungeon_map=result_map

def set_dungeon_open_date(cube_id, open_date):
    if open_date is not None:
        dungeon_map["open_date"]=open_date
    else:
        dungeon_map["open_date"]= getFirstShadowArmyAttack(cube_id)





async def issue_warning(username, msg):
    guild = bot.get_guild(GUILD_ID)
    channel = guild.get_channel(CHANNEL_ID)

    discord_name = normalize_name(username)
    user_id = None
    if discord_name:
        for member in guild.members:
            display_name = member.nick if member.nick else member.global_name or member.name
            display_name = normalize_name(display_name)
            if display_name.lower() == discord_name.lower():
                user_id = member.id
                break

    if user_id:
        mention = f"<@{user_id}>"
    else:
        mention = f"__**@{username}**__"  # fallback if ID not found

    message = f"{mention} {msg}"
    await channel.send(message)
def is_valid_time_format(time_str):
    pattern = r"^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$"
    return re.match(pattern, time_str) is not None

def save_nodes():
    with open(SETTINGS_FILE, "w") as f:
        json.dump(nodes, f, indent=2)

intents = discord.Intents.default()
intents.message_content=True
intents.members=True

bot = commands.Bot(command_prefix="None",intents=intents)

@tasks.loop(minutes=loop_time)
async def dungeon_task():
    global warnings
    logger.info("Running dungeon check...")
    await run_task()
    for username in warnings:
        await issue_warning(username,warnings[username])
    warnings={}
    with open(MAP_FILE, "w") as f:
        json.dump(dungeon_map, f, indent=2)  


@dungeon_task.before_loop
async def before_task():
    await bot.wait_until_ready()

@bot.tree.command(name="run", description="Start the dungeon task", guild=guild)
async def run(interaction: discord.Interaction):
    if not dungeon_task.is_running():
        global GUILD_ID 
        global CHANNEL_ID 
        GUILD_ID = interaction.guild.id
        CHANNEL_ID = interaction.channel.id

        dungeon_task.start()
        await interaction.response.send_message("✅ Dungeon task started!")
    else:
        await interaction.response.send_message("⚠️ Task is already running!")

@bot.tree.command(name="stop", description="Stop the dungeon task", guild=guild)
async def stop(interaction: discord.Interaction):
    if dungeon_task.is_running():
        dungeon_task.stop()
        await interaction.response.send_message("🛑 Dungeon task stopped!")
    else:
        await interaction.response.send_message("⚠️ Task is not running!")
        
@bot.tree.command(name="status", description="Check if the dungeon task loop is running", guild=guild)
async def status(interaction: discord.Interaction):
    if dungeon_task.is_running():
        await interaction.response.send_message("✅ The dungeon task is currently running!")
    else:
        await interaction.response.send_message("⚠️ The dungeon task is NOT running.")
        
@bot.tree.command(name="clear", description="Clear dungeon data", guild=guild)
async def clear(interaction: discord.Interaction):
    global dungeon_map
    dungeon_map = {}
    await interaction.response.send_message("⚠️ Data Cleared!")

@bot.tree.command(name="set_second_node_time", description="Set attack time for second node (ID 6)", guild=guild)
@app_commands.describe(time_str="Time in HH:MM:SS format")
async def set_second_node_time(interaction: discord.Interaction, time_str: str):
    if not is_valid_time_format(time_str):
        await interaction.response.send_message("❌ Invalid format. Use HH:MM:SS (example: 18:00:00)")
        return

    nodes["6"] = time_str
    save_nodes()
    await interaction.response.send_message(f"✅ Second node (6) attack time set to {time_str}")

@bot.tree.command(name="set_last_node_time", description="Set attack time for last node (ID 11)", guild=guild)
@app_commands.describe(time_str="Time in HH:MM:SS format")
async def set_last_node_time(interaction: discord.Interaction, time_str: str):
    if not is_valid_time_format(time_str):
        await interaction.response.send_message("❌ Invalid format. Use HH:MM:SS (example: 18:00:00)")
        return

    nodes["11"] = time_str
    save_nodes()
    await interaction.response.send_message(f"✅ Last node (11) attack time set to {time_str}")

@bot.tree.command(name="get_node_times", description="Display all node attack times", guild=guild)
async def get_node_times(interaction: discord.Interaction):
    message = "📊 **Current Node Attack Times:**\n\n"
    for node_id, time_value in nodes.items():
        node_name = node_names.get(node_id, "Unknown Node")
        message += f"• **{node_name} (ID: {node_id})** → `{time_value}`\n"
    await interaction.response.send_message(message)

@bot.tree.command(name="set_interval", description="Set the dungeon task loop interval in minutes", guild=guild)
@app_commands.describe(minutes="Minutes for the loop interval (must be > 0)")
async def set_interval(interaction: discord.Interaction, minutes: int):
    if minutes <= 0:
        await interaction.response.send_message("❌ Interval must be > 0")
        return

    if dungeon_task.is_running():
        dungeon_task.change_interval(minutes=minutes)
        await interaction.response.send_message(f"⏱️ Loop interval set to {minutes} minute(s)")
    else:
        await interaction.response.send_message("⏱️ Task must be running to change loop interval")

@bot.tree.command(name="get_interval", description="Get the current dungeon loop interval", guild=guild)
async def get_interval(interaction: discord.Interaction):
    if dungeon_task.is_running():
        interval_minutes = dungeon_task.minutes
        await interaction.response.send_message(f"⏱️ Current loop interval is {interval_minutes} minute(s)")
    else:
        await interaction.response.send_message("⚠️ The task is not currently running")

@bot.tree.command(name="help", description="Show all bot commands", guild=guild)
async def help_command(interaction: discord.Interaction):
    message = "📌 **Available Commands:**\n"

    # Use get_commands(guild=guild) to only include your guild commands
    guild_commands = bot.tree.get_commands(guild=guild)
    for command in guild_commands:
        # Only include commands that have a description (skip hidden/invalid ones)
        if command.description:
            message += f"/{command.name} - {command.description}\n"

    await interaction.response.send_message(message, ephemeral=True)
@bot.event
async def on_ready():
    await bot.tree.sync(guild=guild)  # fast guild sync
    logger.info(f"Logged in as {bot.user}")

webserver.keep_alive()
bot.run(TOKEN)