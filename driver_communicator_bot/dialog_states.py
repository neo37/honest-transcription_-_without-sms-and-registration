"""Состояния диалога FSM"""
from aiogram.fsm.state import State, StatesGroup


class VehicleRegistration(StatesGroup):
    """Состояния регистрации машины"""
    waiting_for_readiness = State()  # Ожидание подтверждения готовности
    waiting_for_date = State()  # Ожидание даты готовности
    waiting_for_crew = State()  # Ожидание типа экипажа
    waiting_for_vehicle_type = State()  # Ожидание типа ТС
    waiting_for_capacity = State()  # Ожидание грузоподъемности/объема
    waiting_for_location = State()  # Ожидание местоположения
    waiting_for_destination = State()  # Ожидание региона назначения
    waiting_for_kazan_permit = State()  # Ожидание информации о пропуске в Казань
    waiting_for_driver_name = State()  # Ожидание ФИО водителя
    waiting_for_phone = State()  # Ожидание контактных данных
    completed = State()  # Регистрация завершена

