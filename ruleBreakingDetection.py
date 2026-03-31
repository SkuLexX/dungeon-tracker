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


logging.basicConfig(level=logging.INFO)  # or DEBUG for more detail
logger = logging.getLogger(__name__)
game_offset = timedelta(hours=5, minutes=30)

df_settings = {
    "nodes": {
        "6": "12:00:00",
        "11": "18:00:00"
    },
    "dungeon_news_channel_id": os.environ["DUNGEON_NEWS_CHANNEL_ID"],
    "notification_role_id": os.environ["NOTIFICATION_ROLE_ID"],
    "management_role_id": os.environ["MANAGEMENT_ROLE_ID"],
    "gribs_nuke_time":"21:00:00",
    "channel_id":os.environ["CHANNEL_ID"],
    "check_invalid_attacks":"True"
}

num_map = {
    "1": "first",
    "2": "second",
    "3": "third",
    "4": "fourth"
}
MAP_FILE=os.environ["MAP_FILE"]
STATUS_FILE=os.environ["STATUS_FILE"]
BOSS_STATUS_FILE=os.environ["BOSS_STATUS_FILE"]

SETTINGS_FILE =os.environ["SETTINGS_FILE"]
node_names = {
    "6": "Second Node",
    "11": "Last Node",
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
    with open(STATUS_FILE, "r") as f:
        dungeon_status = json.load(f)
except FileNotFoundError:
    dungeon_status = {
        "The Polyhedral Crucible": "-1",
        "Castle of the Fallen Prince": "-1",
        "Shadowbridge Warrens": "-1"
    }


try:
    with open(BOSS_STATUS_FILE, "r") as f:
        boss_status = {k: bool(v) for k, v in json.load(f).items()}
except FileNotFoundError:
    boss_status = {
        "The Polyhedral Crucible": False,
        "Castle of the Fallen Prince": False,
        "Shadowbridge Warrens": False
    }

try:
    with open(SETTINGS_FILE, "r") as f:
        settings = json.load(f)
except FileNotFoundError:
    settings={}

settings = {**df_settings, **settings}

nodes = settings.get("nodes")
dungeon_news_channel_id=int(settings.get("dungeon_news_channel_id"))
notification_role_id=int(df_settings.get("notification_role_id"))
management_role_id=int(df_settings.get("management_role_id"))
gribs_nuke_time=settings.get("gribs_nuke_time")

check_invalid_attacks=bool(settings.get("check_invalid_attacks"))



actual_node_times={}
TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = int(settings.get("channel_id"))


CUBE = "The Polyhedral Crucible"
HARD = "Castle of the Fallen Prince"
NORMAL = "Shadowbridge Warrens"

DUNGEON_TYPES = [
    {
        "key": CUBE,
        "boss_location_id": 14,
        "dead_msg": "Cube Boss is DEAD",
        "up_msg": "Cube Dungeon is UP",
        "boss_up_msg": "Cube Boss is UP",
    },
    {
        "key": HARD,
        "boss_location_id": 10,
        "dead_msg": "Hard Boss is DEAD",
        "up_msg": "Hard Dungeon is UP",
        "boss_up_msg": "Hard Boss is UP",
    },
    {
        "key": NORMAL,
        "boss_location_id": 5,
        "dead_msg": "Normal Boss is DEAD",
        "up_msg": "Normal Dungeon is UP",
        "boss_up_msg": "Normal Boss is UP",
    },
]

match_number = {
    "6":4,
    "11":1
}
cookie_dict=json.loads(os.environ["COOKIES"])
loop_time = int(os.environ["LOOP_TIME"]) or 10
GUILD_ID = int(os.environ["GUILD_ID"])  # your server ID
guild = discord.Object(id=GUILD_ID)

warnings = {}

def get_dungeon_data(dungeon,soup,id_only=False):
    # Find first div.card that contains the text "The Polyhedral Crucible"
    card = None
    opened_date = None

    for div in soup.find_all("div", class_="card"):
        header = div.find("div", class_="h")
        if header and dungeon in header.text:
            card = div
            break

    if card is None:
        return "-2"    
    enter_btn = card.find("a", string="Enter")
    
    if enter_btn is None:
        return "-1"    
    href = enter_btn["href"]
    parsed = urlparse(href)
    id_value = parse_qs(parsed.query)["id"][0]
    if id_only:
        return id_value
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

    


def normalize_name(name):
    # Remove tags like [tag]
    name = re.sub(r"\[.*?\]", "", name, flags=re.IGNORECASE)
    # Remove all spaces 
    name = re.sub(r"\s+", "", name)
    # Remove common separators
    name = re.sub(r"[-_.]", "", name)
    return name.lower().strip()

def get_game_time():
    utc_now = datetime.utcnow()
    # Current game time
    game_time_now = utc_now + game_offset

    # Format as YYYY-MM-DD HH:MM:SS
    game_time_str = game_time_now.strftime("%Y-%m-%d %H:%M:%S")

    logger.info(f"Game time now : {game_time_str}")
    return game_time_str

def is_game_time(target: str) -> bool:
    """target format: 'HH:MM:SS'"""
    utc_now = datetime.utcnow()
    game_time_now = utc_now + game_offset
    
    target_time = datetime.strptime(target, "%H:%M:%S").time()
    
    return game_time_now.time().replace(second=0, microsecond=0) == \
           target_time.replace(second=0)

def compare_times(t1: str, t2: str) -> bool:
    fmt = "%Y-%m-%d %H:%M:%S"
    dt1 = datetime.strptime(t1, fmt).replace(second=0)
    dt2 = datetime.strptime(t2, fmt).replace(second=0)
    return dt1 == dt2


def getFirstShadowArmyAttack(cube_id):
    attackDates = []
    for match_no in map(str, range(1, FIRST_NODE_MATCH_NO + 1)):
        attackDates.extend([player["joined_at"] for player in getAttackers(FIRST_NODE,cube_id,match_no)])
    min_date = min(attackDates) if attackDates else get_game_time()
    return min_date

def update_node_times():
    for node in nodes:
        actual_node_times[node]=combine_date_and_time(dungeon_map["open_date"],nodes[node])
    logger.info(f"node-times : {actual_node_times}")

    
def getDungeonCubeStatus():
    soup = get_guild_dungeon_soup()
    cube_id,open_date = get_dungeon_data(CUBE,soup)
    return cube_id,open_date

def get_guild_dungeon_soup():
    url = "https://demonicscans.org/guild_dungeon.php"
    return get_soup(url)

def get_soup(url):
    res = requests.get(url, cookies=cookie_dict)
    soup = BeautifulSoup(res.text, "html.parser")
    return soup

async def run_task():
    global dungeon_map
    global cookie_dict
    cube_id,open_date = getDungeonCubeStatus()
    if cube_id == "-1" or cube_id == "-2":
        return
    if "dungeon_id" not in dungeon_map or  dungeon_map["dungeon_id"] != cube_id: #new dungeon opened
        dungeon_map={}
        set_dungeon_open_date(cube_id, open_date)
    if "open_date" not in dungeon_map:
        set_dungeon_open_date(cube_id, open_date)

    
    open_date = dungeon_map["open_date"]
    logger.info(f"open_date : {open_date}")
    update_node_times()
    result_map ={}
    result_map["dungeon_id"]=cube_id
    result_map["open_date"]=open_date
    if check_invalid_attacks:
        for node_id in nodes:
            if node_id in dungeon_map:
                node_map = dungeon_map[node_id]
            else:
                node_map={}
            threshold_date_str = actual_node_times[node_id]
            node_map = getInvalidAttacks(threshold_date_str,node_id,node_map,cube_id)
            result_map[node_id] = node_map
        dungeon_map=result_map
    else:
        dungeon_map["dungeon_id"]=cube_id
        dungeon_map["open_date"]=open_date

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

def save_settings():
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)

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
    global check_invalid_attacks
    if not check_invalid_attacks:
        global GUILD_ID 
        global CHANNEL_ID 
        GUILD_ID = interaction.guild.id
        CHANNEL_ID = interaction.channel.id

        check_invalid_attacks=True
        settings["check_invalid_attacks"]=True
        settings["channel_id"]=CHANNEL_ID

        save_settings()
        await interaction.response.send_message("✅ Dungeon task started!", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ Task is already running!", ephemeral=True)

@bot.tree.command(name="stop", description="Stop the dungeon task", guild=guild)
async def stop(interaction: discord.Interaction):
    global check_invalid_attacks
    if check_invalid_attacks:
        check_invalid_attacks=False
        settings["check_invalid_attacks"]=False
        save_settings()
        await interaction.response.send_message("🛑 Dungeon task stopped!", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ Task is not running!", ephemeral=True)

@bot.tree.command(name="status", description="Check if the dungeon task loop is running", guild=guild)
async def status(interaction: discord.Interaction):
    if check_invalid_attacks:
        await interaction.response.send_message("✅ The dungeon task is currently running!", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ The dungeon task is NOT running.", ephemeral=True)
        
@bot.tree.command(name="clear", description="Clear dungeon data", guild=guild)
async def clear(interaction: discord.Interaction):
    global dungeon_map
    dungeon_map = {}
    with open(MAP_FILE, "w") as f:
        json.dump(dungeon_map, f, indent=2)  
    await interaction.response.send_message("⚠️ Data Cleared!")

@bot.tree.command(name="set_second_node_time", description="Set attack time for second node (ID 6)", guild=guild)
@app_commands.describe(time_str="Time in HH:MM:SS format")
async def set_second_node_time(interaction: discord.Interaction, time_str: str):
    if not is_valid_time_format(time_str):
        await interaction.response.send_message("❌ Invalid format. Use HH:MM:SS (example: 18:00:00)", ephemeral=True)
        return

    nodes["6"] = time_str
    save_settings()
    await interaction.response.send_message(f"✅ Second node (6) attack time set to {time_str}", ephemeral=True)

@bot.tree.command(name="set_last_node_time", description="Set attack time for last node (ID 11)", guild=guild)
@app_commands.describe(time_str="Time in HH:MM:SS format")
async def set_last_node_time(interaction: discord.Interaction, time_str: str):
    if not is_valid_time_format(time_str):
        await interaction.response.send_message("❌ Invalid format. Use HH:MM:SS (example: 18:00:00)", ephemeral=True)
        return

    nodes["11"] = time_str
    save_settings()
    await interaction.response.send_message(f"✅ Last node (11) attack time set to {time_str}", ephemeral=True)

@bot.tree.command(name="get_node_times", description="Display all node attack times", guild=guild)
async def get_node_times(interaction: discord.Interaction):
    message = "📊 **Current Node Attack Times:**\n\n"
    for node_id, time_value in nodes.items():
        node_name = node_names.get(node_id, "Unknown Node")
        message += f"• **{node_name} (ID: {node_id})** → `{time_value}`\n"
    await interaction.response.send_message(message, ephemeral=True)

@bot.tree.command(name="set_interval", description="Set the dungeon task loop interval in minutes", guild=guild)
@app_commands.describe(minutes="Minutes for the loop interval (must be > 0)")
async def set_interval(interaction: discord.Interaction, minutes: int):
    if minutes <= 0:
        await interaction.response.send_message("❌ Interval must be > 0", ephemeral=True)
        return

    dungeon_task.change_interval(minutes=minutes)
    await interaction.response.send_message(f"⏱️ Loop interval set to {minutes} minute(s)", ephemeral=True)

@bot.tree.command(name="get_interval", description="Get the current dungeon loop interval", guild=guild)
async def get_interval(interaction: discord.Interaction):
    interval_minutes = dungeon_task.minutes
    await interaction.response.send_message(f"⏱️ Current loop interval is {interval_minutes} minute(s)", ephemeral=True)

@bot.tree.command(name="set_gribs_nuke_time", description="Set Nuke time for gribs", guild=guild)
@app_commands.describe(time_str="Time in HH:MM:SS format")
async def set_gribs_nuke_time(interaction: discord.Interaction, time_str: str):
    global gribs_nuke_time
    if not is_valid_time_format(time_str):
        await interaction.response.send_message("❌ Invalid format. Use HH:MM:SS (example: 18:00:00)", ephemeral=True)
        return

    settings["gribs_nuke_time"] = time_str
    gribs_nuke_time = time_str
    save_settings()
    await interaction.response.send_message(f"✅ Last node (11) attack time set to {time_str}", ephemeral=True)

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



@tasks.loop(minutes=1)
async def scheduled_message():
    if("open_date" not in dungeon_map or dungeon_map["open_date"] is None):
        return
    game_time=get_game_time()
    update_node_times()
    for node_id in actual_node_times:
        if compare_times(actual_node_times[node_id],game_time):
            await send_notification(f"Attack {node_names[node_id]} Shadow Army!")

async def send_notification(msg,role_id = notification_role_id):
    channel = bot.get_channel(dungeon_news_channel_id)
    if channel:
        if notification_role_id:
            guild = bot.get_guild(GUILD_ID)
            role = guild.get_role(notification_role_id) if notification_role_id else None
            role_mention = role.mention if role else "@everyone"
            await channel.send(f"{role_mention} {msg}")
        else:
            await channel.send(msg)


@tasks.loop(minutes=1)
async def check_dungeon_status():
    soup = get_guild_dungeon_soup()
    save_dungeon_status = False
    save_boss_status = False

    for d in DUNGEON_TYPES:
        key = d["key"]
        new_id = get_dungeon_data(key, soup, True)
        if new_id=="-2":
            continue
        url = f"https://demonicscans.org/guild_dungeon_location.php?instance_id={new_id}&location_id={d['boss_location_id']}"

        # --- Dungeon status ---
        if new_id != dungeon_status[key]:
            save_dungeon_status = True
            if new_id == "-1":
                await send_notification(d["dead_msg"],management_role_id)
            else:
                await send_notification(d["up_msg"])
        dungeon_status[key] = new_id

        # --- Boss status ---
        if new_id != "-1":
            new_boss_status = get_status_is_ok(url)
            if not boss_status[key] and new_boss_status:
                await send_notification(d["boss_up_msg"])
                await send_notification(f"Join Now {url}", None)
            save_boss_status = boss_status[key]!=new_boss_status
            boss_status[key] = new_boss_status

    if save_dungeon_status:
        with open(STATUS_FILE, "w") as f:
            json.dump(dungeon_status, f, indent=2)

    if save_boss_status:
        with open(BOSS_STATUS_FILE, "w") as f:
            json.dump(boss_status, f, indent=2)
    
    
@tasks.loop(minutes=1)
async def check_gribbs_status():
    if(is_game_time(gribs_nuke_time)):
        await send_notification("Nuke gribs")


def get_status_is_ok(url):
    res = requests.get(url, cookies=cookie_dict)
    status = res.status_code
    return status==200






@scheduled_message.before_loop
async def before_scheduled():
    await bot.wait_until_ready()

# Start it when bot is ready

@bot.event
async def on_ready():
    await bot.tree.sync(guild=guild)  # fast guild sync
    scheduled_message.start()
    dungeon_task.start()
    check_dungeon_status.start()
    check_gribbs_status.start()
    logger.info(f"Logged in as {bot.user}")

bot.run(TOKEN)