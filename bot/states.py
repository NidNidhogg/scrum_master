"""
bot/states.py – FSM State definitions for the bot.
"""
from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    waiting_for_draft_edit = State()
