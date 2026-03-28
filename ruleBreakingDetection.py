import requests
from playwright.async_api import async_playwright
from dotenv import load_dotenv
import os
import time
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import json
from datetime import datetime
import discord
from discord.ext import commands, tasks
import re


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


TOKEN = os.environ["BOT_TOKEN"]
GUILD_ID  = None
CHANNEL_ID = None


CUBE = "The Polyhedral Crucible"
HARD = "Castle of the Fallen Prince"
NORMAL = "Shadowbridge Warrens"

WEBHOOK_URL = os.environ["WEBHOOK_URL"]

match_number = {
    "6":4,
    "11":1
}
EMAIL = os.environ["EMAIL"]
PASSWORD = os.environ["PASSWORD"]
cookie_dict={}

warnings = {}

async def wait_for_cookies(context, timeout=5):
    start = time.time()

    while time.time() - start < timeout:
        cookies =await context.cookies()
        if cookies:
            return
        time.sleep(0.5)
    raise Exception("Cookies not found")


async def getCookies():
    global cookie_dict
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://demonicscans.org/signin.php")

        await page.fill('input[name="email"]', EMAIL)
        await page.fill('input[name="password"]', PASSWORD)
        await page.click('input[type="submit"]')

        # wait for cookies (you need async version of your function)
        await wait_for_cookies(context)

        cookies = await context.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        await browser.close()


def getDungeonId(dungeon,soup):
    # Find first div.card that contains the text "The Polyhedral Crucible"
    card = None
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
    return id_value


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
        print(f"⚠️ New attacker: {name}, damage={dmg}, last_action={last_action}")
        attacker_map[name] = {"damage": dmg, "last_action": last_action}
        return True
    else:
        old = attacker_map[name]
        old_dmg = old["damage"]
        old_time = parse_time(old["last_action"])
        if dmg > old_dmg and new_time > old_time:
            print(f"⚠️ Updated attacker: {name}, dmg {old_dmg}→{dmg}, last_action {old_time}→{last_action}")
            attacker_map[name] = {"damage": dmg, "last_action": last_action}
            return True
    return False



def getAttackers(threshold_time_str,node_id,node_map,cube_id):
    threshold_time = datetime.strptime(threshold_time_str, "%H:%M:%S").time()
    max_match_no = match_number[node_id]
    for match_no in map(str, range(1, max_match_no + 1)):
        STATS_URL = "https://demonicscans.org/guild_dungeon_cube_army_action.php"
        data = {
            "action": "contributors",
            "instance_id": cube_id,
            "node_id": node_id,
            "match_no":match_no
        }
        res = requests.post(STATS_URL,data=data, cookies=cookie_dict)



        json_data =res.text  # your full JSON here

        data = json.loads(json_data)
        if match_no in node_map:
            match_map=node_map[match_no]
        else:
            match_map={}
        for attacker in data.get("board").get("attackers", []):
            last_action_str = attacker.get("last_action_at")  # e.g., "2026-03-27 07:26:58"
            last_action_time = datetime.strptime(last_action_str, "%Y-%m-%d %H:%M:%S").time()
            
            if last_action_time < threshold_time:
                if updateMap(attacker.get("username"),attacker.get("damage_dealt"),last_action_str,match_map):
                    queue_warning(attacker,node_id,match_no,threshold_time_str)

        node_map[match_no]=match_map
    
    return node_map

    




async def run_task():
    global dungeon_map
    global cookie_dict
    await getCookies()
    url = "https://demonicscans.org/guild_dungeon.php"
    res = requests.get(url, cookies=cookie_dict)
    soup = BeautifulSoup(res.text, "html.parser")
    cube_id = getDungeonId(CUBE,soup)
    if cube_id == -1:
        return
    if "dungeon_id" not in dungeon_map or  dungeon_map["dungeon_id"] != cube_id:
        dungeon_map={}

    result_map ={}
    result_map["dungeon_id"]=cube_id
    for node_id in nodes:
        if node_id in dungeon_map:
            node_map = dungeon_map[node_id]
        else:
            node_map={}
        threshold_time_str = nodes[node_id]
        node_map = getAttackers(threshold_time_str,node_id,node_map,cube_id)
        result_map[node_id] = node_map
    dungeon_map=result_map





async def issue_warning(username, msg):
    guild = bot.get_guild(GUILD_ID)
    channel = guild.get_channel(CHANNEL_ID)

    discord_name =username
    user_id = None
    if discord_name:
        for member in guild.members:
            display_name = member.nick if member.nick else member.global_name or member.name
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

bot = commands.Bot(command_prefix="!",intents=intents)

@tasks.loop(minutes=1)
async def dungeon_task():
    global warnings
    print("Running dungeon check...")
    await run_task()
    for username in warnings:
        await issue_warning(username,warnings[username])
    warnings={}
    with open(MAP_FILE, "w") as f:
        json.dump(dungeon_map, f, indent=2)  


@dungeon_task.before_loop
async def before_task():
    await bot.wait_until_ready()

@bot.command()
async def run(ctx):
    if not dungeon_task.is_running():
        global GUILD_ID 
        global CHANNEL_ID 
        GUILD_ID = ctx.guild.id
        CHANNEL_ID = ctx.channel.id

        dungeon_task.start()
        await ctx.send("✅ Dungeon task started!")
    else:
        await ctx.send("⚠️ Task is already running!")
@bot.command()
async def stop(ctx):
    if dungeon_task.is_running():
        dungeon_task.stop()
        await ctx.send("🛑 Dungeon task stopped!")
    else:
        await ctx.send("⚠️ Task is not running!")

@bot.command()
async def clear(ctx):
    global dungeon_map
    dungeon_map={}
    await ctx.send("⚠️ Data Cleared!")
@bot.command()
async def set_second_node_time(ctx, time_str: str):
    if not is_valid_time_format(time_str):
        await ctx.send("❌ Invalid format. Use HH:MM:SS (example: 18:00:00)")
        return

    nodes["6"] = time_str
    save_nodes()
    await ctx.send(f"✅ Second node (6) attack time set to {time_str}")

# -------------------------------
# Command: set node 11 time
# -------------------------------
@bot.command()
async def set_last_node_time(ctx, time_str: str):
    if not is_valid_time_format(time_str):
        await ctx.send("❌ Invalid format. Use HH:MM:SS (example: 18:00:00)")
        return

    nodes["11"] = time_str
    save_nodes()
    await ctx.send(f"✅ Last node (11) attack time set to {time_str}")

@bot.command()
async def get_node_times(ctx):
    message = "📊 **Current Node Attack Times:**\n\n"

    for node_id, time_value in nodes.items():
        node_name = node_names.get(node_id, "Unknown Node")
        message += f"• **{node_name} (ID: {node_id})** → `{time_value}`\n"

    await ctx.send(message)
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


bot.run(TOKEN)