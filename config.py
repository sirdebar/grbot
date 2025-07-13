
import os
import logging
import pytz
from dotenv import load_dotenv

load_dotenv()

# Bot configuration
TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))

# Logging configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Conversation states
WAITING_FOR_TOPIC_NAME = 1
WAITING_FOR_TOPIC_ID = 2
WAITING_FOR_BROADCAST = 3
WAITING_FOR_RENAME_COUNT = 4
WAITING_FOR_RENAME = 5
WAITING_FOR_PC_SELECTION = 6
WAITING_FOR_BREAK_NAME = 7
WAITING_FOR_BREAK_START_TIME = 8
WAITING_FOR_BREAK_START_TEXT = 9
WAITING_FOR_BREAK_END_TIME = 10
WAITING_FOR_BREAK_END_TEXT = 11
WAITING_FOR_COMPLAINT_TARGET = 12
WAITING_FOR_COMPLAINT_REASON = 13
WAITING_FOR_SUPPORT_MESSAGE = 14
WAITING_FOR_ADMIN_RESPONSE = 15

# Global variables
topics_dict = {}
workers_dict = {}
rename_topics_dict = {}
sos_words = {"сос", "sos", "помогите", "помощь", "номер"}
active_topics = {}
active_topics_info = {}
sos_activation_times = {}
sos_removal_tasks = {}
sos_update_tasks = {}
restricted_topics = {}
admin_list = set()
breaks_dict = {}
break_id_counter = 1
break_tasks = {}
pc_mode_enabled = True
pending_complaints = {}
support_tickets = {}
ticket_id_counter = 1

# Timezone
KYIV_TZ = pytz.timezone('Europe/Kiev')
