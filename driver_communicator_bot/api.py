"""REST API для связи с CPQ системой"""
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from database import Vehicle, VehicleStatus, RouteOffer, async_session_maker
from config import API_HOST, API_PORT
from contextlib import asynccontextmanager


@asynccontextmanager
async def get_session():
    """Получение сессии БД для FastAPI"""
    async with async_session_maker() as session:
        yield session

app = FastAPI(title="Vehicle Registration API", version="1.0.0")

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class VehicleResponse(BaseModel):
    """Модель ответа с данными о машине"""
    id: int
    telegram_user_id: int
    telegram_username: Optional[str]
    is_ready: bool
    ready_date: Optional[datetime]
    crew_type: Optional[str]
    vehicle_type: Optional[str]
    capacity_cubic_meters: Optional[int]
    current_location: Optional[str]
    destination_region: Optional[str]
    has_kazan_permit: Optional[bool]
    driver_name: Optional[str]
    driver_phone: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RouteOfferRequest(BaseModel):
    """Запрос на создание предложения маршрута"""
    vehicle_id: int
    route_id: str
    route_description: Optional[str] = None
    reserved_until: Optional[datetime] = None


class RouteOfferResponse(BaseModel):
    """Ответ с данными о предложенном маршруте"""
    id: int
    vehicle_id: int
    route_id: str
    route_description: Optional[str]
    reserved_until: Optional[datetime]
    is_selected: bool
    created_at: datetime

    class Config:
        from_attributes = True


@app.get("/")
async def root():
    """Корневой endpoint"""
    return {"message": "Vehicle Registration API", "version": "1.0.0"}


@app.get("/vehicles", response_model=List[VehicleResponse])
async def get_free_vehicles(
    status: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """
    Получение списка машин
    
    Args:
        status: Фильтр по статусу (free, reserved, in_work, etc.)
        session: Сессия БД
    """
    query = select(Vehicle)
    
    if status:
        try:
            status_enum = VehicleStatus(status)
            query = query.where(Vehicle.status == status_enum)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    
    result = await session.execute(query)
    vehicles = result.scalars().all()
    return vehicles


@app.get("/vehicles/{vehicle_id}", response_model=VehicleResponse)
async def get_vehicle(
    vehicle_id: int,
    session: AsyncSession = Depends(get_session)
):
    """Получение информации о конкретной машине"""
    vehicle = await session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle


@app.post("/vehicles/{vehicle_id}/offers", response_model=RouteOfferResponse)
async def create_route_offer(
    vehicle_id: int,
    offer: RouteOfferRequest,
    session: AsyncSession = Depends(get_session)
):
    """Создание предложения маршрута для машины"""
    vehicle = await session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    route_offer = RouteOffer(
        vehicle_id=vehicle_id,
        route_id=offer.route_id,
        route_description=offer.route_description,
        reserved_until=offer.reserved_until
    )
    
    session.add(route_offer)
    await session.commit()
    await session.refresh(route_offer)
    
    return route_offer


@app.get("/vehicles/{vehicle_id}/offers", response_model=List[RouteOfferResponse])
async def get_vehicle_offers(
    vehicle_id: int,
    session: AsyncSession = Depends(get_session)
):
    """Получение всех предложенных маршрутов для машины"""
    vehicle = await session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    query = select(RouteOffer).where(RouteOffer.vehicle_id == vehicle_id)
    result = await session.execute(query)
    offers = result.scalars().all()
    return offers


@app.put("/vehicles/{vehicle_id}/status")
async def update_vehicle_status(
    vehicle_id: int,
    status: str,
    session: AsyncSession = Depends(get_session)
):
    """Обновление статуса машины"""
    vehicle = await session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    try:
        status_enum = VehicleStatus(status)
        vehicle.status = status_enum
        await session.commit()
        return {"message": "Status updated", "vehicle_id": vehicle_id, "status": status}
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")


@app.put("/offers/{offer_id}/select")
async def select_route_offer(
    offer_id: int,
    session: AsyncSession = Depends(get_session)
):
    """Выбор маршрута (снятие брони с остальных)"""
    offer = await session.get(RouteOffer, offer_id)
    if not offer:
        raise HTTPException(status_code=404, detail="Route offer not found")
    
    # Помечаем выбранный маршрут
    offer.is_selected = True
    
    # Снимаем бронь с остальных маршрутов для этой машины
    query = select(RouteOffer).where(
        RouteOffer.vehicle_id == offer.vehicle_id,
        RouteOffer.id != offer_id
    )
    result = await session.execute(query)
    other_offers = result.scalars().all()
    
    for other_offer in other_offers:
        other_offer.reserved_until = None  # Снимаем бронь
    
    # Обновляем статус машины
    vehicle = await session.get(Vehicle, offer.vehicle_id)
    if vehicle:
        vehicle.status = VehicleStatus.RESERVED
    
    await session.commit()
    return {"message": "Route selected", "offer_id": offer_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=API_HOST, port=API_PORT)

