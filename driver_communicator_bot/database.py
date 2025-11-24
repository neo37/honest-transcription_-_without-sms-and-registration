"""Модели базы данных"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey, Enum as SQLEnum
from datetime import datetime
import enum
from config import DATABASE_URL

Base = declarative_base()
engine = create_async_engine(DATABASE_URL, echo=True)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class VehicleStatus(enum.Enum):
    """Статусы машины"""
    COLLECTING_DATA = "collecting_data"  # Сбор данных
    FREE = "free"  # Свободна
    RESERVED = "reserved"  # Забронирована
    IN_WORK = "in_work"  # В работе
    COMPLETED = "completed"  # Исполнена


class VehicleType(enum.Enum):
    """Типы транспортных средств"""
    REFRIGERATOR = "refrigerator"  # Рефрижератор
    TENT = "tent"  # Тентованный
    OPEN = "open"  # Открытый
    CONTAINER = "container"  # Контейнер
    TANK = "tank"  # Цистерна
    OTHER = "other"  # Другое


class CrewType(enum.Enum):
    """Тип экипажа"""
    SINGLE = "single"  # Одиночный
    PAIR = "pair"  # Парный


class Vehicle(Base):
    """Модель машины"""
    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True)
    telegram_user_id = Column(Integer, nullable=False, index=True)
    telegram_username = Column(String, nullable=True)
    
    # Данные о машине
    is_ready = Column(Boolean, default=False)
    ready_date = Column(DateTime, nullable=True)
    crew_type = Column(SQLEnum(CrewType), nullable=True)
    vehicle_type = Column(SQLEnum(VehicleType), nullable=True)
    capacity_cubic_meters = Column(Integer, nullable=True)  # Объем в кубах
    current_location = Column(String, nullable=True)  # Текущее местоположение
    destination_region = Column(String, nullable=True)  # Регион назначения
    has_kazan_permit = Column(Boolean, nullable=True)  # Пропуск в Казань
    
    # Данные водителя
    driver_name = Column(String, nullable=True)  # ФИО водителя
    driver_phone = Column(String, nullable=True)  # Контактные данные
    
    # Статус и метаданные
    status = Column(SQLEnum(VehicleStatus), default=VehicleStatus.COLLECTING_DATA)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Связь с заявками
    route_offers = relationship("RouteOffer", back_populates="vehicle")


class RouteOffer(Base):
    """Предложенные маршруты для машины"""
    __tablename__ = "route_offers"

    id = Column(Integer, primary_key=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    route_id = Column(String, nullable=False)  # ID маршрута из CPQ
    route_description = Column(Text, nullable=True)
    reserved_until = Column(DateTime, nullable=True)  # До какого времени забронирован
    is_selected = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    vehicle = relationship("Vehicle", back_populates="route_offers")


async def init_db():
    """Инициализация базы данных"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)



