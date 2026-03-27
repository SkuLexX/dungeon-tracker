import requests
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import os
import time
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import sys
import json
from datetime import datetime
import discord
from discord.ext import commands
import asyncio


if os.path.exists(".env"):
    load_dotenv()

TOKEN = os.environ["BOT_TOKEN"]
GUILD_ID = 1487130013525610687  # your server ID
CHANNEL_ID = 1487130014448226407  # channel to send warnings


CUBE = "The Polyhedral Crucible"
HARD = "Castle of the Fallen Prince"
NORMAL = "Shadowbridge Warrens"

WEBHOOK_URL = os.environ["WEBHOOK_URL"]

nodes = {
    "6":os.environ["SECOND_NODE_ATTACK_TIME"],
    "11":os.environ["THIRD_NODE_ATTACK_TIME"]
}
match_number = {
    "6":4,
    "11":1
}
EMAIL = os.environ["EMAIL"]
PASSWORD = os.environ["PASSWORD"]
cookie_dict={}

warnings = {}

def wait_for_cookies(context, timeout=5):
    start = time.time()

    while time.time() - start < timeout:
        cookies = context.cookies()
        if cookies:
            return
        time.sleep(0.5)
    raise Exception("Cookies not found")



with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    page.goto("https://demonicscans.org/signin.php")

    page.fill('input[name="email"]', EMAIL)
    page.fill('input[name="password"]', PASSWORD)
    page.click('input[type="submit"]')
    wait_for_cookies(page.context)
    cookies = page.context.cookies()
    cookie_dict = {c["name"]: c["value"] for c in cookies}

    browser.close()


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


def queue_warning(username,time,node,match_no):
    message ="⚠️ You did more damage than allowed!"
   
    warnings[username] = message


def parse_time(ts):
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")

def updateMap(name,dmg,last_action,attacker_map):
    new_time = parse_time(last_action)
    if dmg == 0:
        return
    if name not in attacker_map:
        queue_warning(name,last_action,0,0)
        print(f"⚠️ New attacker: {name}, damage={dmg}, last_action={last_action}")
        attacker_map[name] = {"damage": dmg, "last_action": last_action}
    else:
        old = attacker_map[name]
        old_dmg = old["damage"]
        old_time = parse_time(old["last_action"])
        if dmg > old_dmg and new_time > old_time:
            queue_warning(name,last_action,0,0)
            print(f"⚠️ Updated attacker: {name}, dmg {old_dmg}→{dmg}, last_action {old_time}→{last_action}")
            attacker_map[name] = {"damage": dmg, "last_action": last_action}



def getAttackers(threshold_time_str,node_id,dungeon_map,cube_id):
    threshold_time = datetime.strptime(threshold_time_str, "%H:%M:%S").time()
    max_match_no = match_number[node_id]
    for match_no in range(1,max_match_no+1):
        match_no=str(match_no)
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
        if match_no in dungeon_map:
            match_map=dungeon_map[match_no]
        else:
            match_map={}
        for attacker in data.get("board").get("attackers", []):
            last_action_str = attacker.get("last_action_at")  # e.g., "2026-03-27 07:26:58"
            last_action_time = datetime.strptime(last_action_str, "%Y-%m-%d %H:%M:%S").time()
            
            if last_action_time < threshold_time:
                updateMap(attacker.get("username"),attacker.get("damage_dealt"),last_action_str,match_map)
        dungeon_map[match_no]=match_map
    
    return dungeon_map

    
url = "https://demonicscans.org/guild_dungeon.php"

res = requests.get(url, cookies=cookie_dict)


soup = BeautifulSoup(res.text, "html.parser")

MAP_FILE = "map.json"

try:
    with open(MAP_FILE, "r") as f:
        dungeon_map = json.load(f)
except FileNotFoundError:
    dungeon_map = {}

cube_id = getDungeonId(CUBE,soup)
if cube_id == -1:
    sys.exit(0)
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
with open(MAP_FILE, "w") as f:
    json.dump(result_map, f, indent=2)  

intents = discord.Intents.default()
intents.message_content=True
intents.members=True

bot = commands.Bot(command_prefix="!",intents=intents)


async def issue_warning(username, msg):
    guild = bot.get_guild(GUILD_ID)
    channel = guild.get_channel(CHANNEL_ID)

    discord_name =username
    user_id = None
    print(guild.members)
    if discord_name:
        for member in guild.members:
            display_name = member.nick if member.nick else member.global_name or member.name
            if display_name.lower() == discord_name.lower():
                user_id = member.id
                break

    if user_id:
        mention = f"<@{user_id}>"
    else:
        mention = f"@{username}"  # fallback if ID not found

    message = f"{mention} {msg}"
    await channel.send(message)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    for username in warnings:
        await issue_warning(username,warnings[username])

    # Close bot after sending messages (useful for GitHub Actions)
    await bot.close()

bot.run(TOKEN)