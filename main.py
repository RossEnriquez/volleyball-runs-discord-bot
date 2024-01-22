import discord
from discord.ext import commands
import config
from config import *
import os
import firebase_admin
from firebase_admin import credentials, firestore
import datetime
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# discord config
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='$', intents=intents)

# firebase config
firebase_config = config.firebase_config
cred = credentials.Certificate(firebase_config)
db_app = firebase_admin.initialize_app(cred)
db = firestore.client()
locations_ref = db.collection('locations')
messages_ref = db.collection('message_templates')

day_emojis = ["1️⃣ ", "2️⃣ ", "3️⃣ ", "4️⃣ ", "5️⃣ ", "6️⃣ ", "7️⃣ ", "8️⃣ ", "9️⃣ ", "0️⃣ "]
last_voting_msg_id = None


@bot.event
async def on_ready():
    print(f'We have logged in as {bot.user}')


# NOTE: when inputting days, there must be no spaces between commas/numbers
@bot.command(name='start')
async def on_start(ctx, start, days):
    today = datetime.today()
    start_date = datetime.strptime(str(today.year) + start, '%Y%b%d')
    selected_days = days.split(',')
    # insert start date into selected days
    selected_days.insert(0, '0')
    msg_out = messages_ref.document('start').get().to_dict()['message'].replace('\\n', '\n')
    msg_out = msg_out.replace('[start_date]', start_date.strftime('%A `%b %d`'))
    day_msgs = ''

    for i, day_str in enumerate(selected_days[:10]):
        day_offset = int(day_str)
        # find date
        date_of_day = start_date + timedelta(days=day_offset)
        day_msgs += day_emojis[i] + date_of_day.strftime('%A `%b %d`') + '\n'

    msg_out = msg_out.replace('[selected_days]', day_msgs)
    announcement_channel = bot.get_channel(756974131496615936)
    await announcement_channel.send(msg_out)

    global last_voting_msg_id
    last_voting_msg_id = announcement_channel.last_message_id


@bot.command(name='booked')
async def on_booked(ctx, loc, date, time):
    today = datetime.today()
    location = locations_ref.document(loc).get().to_dict()
    booked_date = datetime.strptime(str(today.year) + date, '%Y%b%d')

    reply_msg = f'BOOKED A RUN @everyone\n- {location["name"]}\n- {location["address"]}\n' \
                f'- {booked_date.strftime("%A `%b %d`")} from `{time}`\n\n' + \
                messages_ref.document('booked').get().to_dict()['message'].replace('\\n', '\n')

    announcement_channel = bot.get_channel(756974131496615936)
    last_voting_msg = await announcement_channel.fetch_message(last_voting_msg_id)
    await last_voting_msg.reply(reply_msg)

    event_time = time.split('-')
    start_time = datetime.strptime(event_time[0], '%I%p')
    end_time = datetime.strptime(event_time[1], '%I%p')

    start_datetime = booked_date.replace(hour=start_time.hour, tzinfo=ZoneInfo('America/Toronto'))
    end_datetime = booked_date.replace(hour=end_time.hour, tzinfo=ZoneInfo('America/Toronto'))

    await bot.get_guild(config.SERVER_ID).create_scheduled_event(
        name='Volleyball Runs',
        description='Come to our volleyball runs',
        start_time=start_datetime,
        end_time=end_datetime,
        privacy_level=discord.PrivacyLevel.guild_only,
        entity_type=discord.EntityType.external,
        location=(location['name'] + ', ' + location['address']),
        reason='Booked new volleyball run'
    )


# for testing purposes - can add whatever you want
@bot.command(name='test')
async def test(ctx):
    location = messages_ref.document('start').get().to_dict()
    print(location['message'].replace('\\n', '\n'))


bot.run(config.TOKEN)

