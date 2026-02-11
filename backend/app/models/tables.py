import datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#e53935")
    geometry = mapped_column(Geometry("LINESTRING", srid=4326), nullable=True)

    stops: Mapped[list["RouteStop"]] = relationship(back_populates="route", order_by="RouteStop.order")


class Stop(Base):
    __tablename__ = "stops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    direction: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    geometry = mapped_column(Geometry("POINT", srid=4326), nullable=True)

    route_stops: Mapped[list["RouteStop"]] = relationship(back_populates="stop")


class RouteStop(Base):
    __tablename__ = "route_stops"
    __table_args__ = (
        UniqueConstraint("route_id", "stop_id", "direction", "order", name="uq_route_stop_dir_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    route_id: Mapped[int] = mapped_column(Integer, ForeignKey("routes.id"), nullable=False)
    stop_id: Mapped[int] = mapped_column(Integer, ForeignKey("stops.id"), nullable=False)
    direction: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0=forward, 1=reverse
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_along: Mapped[float] = mapped_column(Float, nullable=True)  # meters from route start

    route: Mapped["Route"] = relationship(back_populates="stops")
    stop: Mapped["Stop"] = relationship(back_populates="route_stops")


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # ETTU DEV_ID
    board_num: Mapped[str] = mapped_column(String(20), nullable=False)
    route_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("routes.id"), nullable=True)
    last_seen: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class VehiclePosition(Base):
    __tablename__ = "vehicle_positions"
    __table_args__ = (
        Index("ix_vp_vehicle_ts", "vehicle_id", "timestamp"),
        Index("ix_vp_route_ts", "route_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[str] = mapped_column(String(64), nullable=False)
    route_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    speed: Mapped[float] = mapped_column(Float, nullable=True)
    course: Mapped[float] = mapped_column(Float, nullable=True)
    progress: Mapped[float] = mapped_column(Float, nullable=True)  # 0.0-1.0 along route
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class TravelTimeSegment(Base):
    __tablename__ = "travel_time_segments"
    __table_args__ = (
        UniqueConstraint(
            "route_id", "from_stop_id", "to_stop_id", "day_type", "hour",
            name="uq_travel_segment"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    route_id: Mapped[int] = mapped_column(Integer, ForeignKey("routes.id"), nullable=False)
    from_stop_id: Mapped[int] = mapped_column(Integer, ForeignKey("stops.id"), nullable=False)
    to_stop_id: Mapped[int] = mapped_column(Integer, ForeignKey("stops.id"), nullable=False)
    day_type: Mapped[str] = mapped_column(String(10), nullable=False)  # weekday, saturday, sunday
    hour: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-23
    median_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    p25_seconds: Mapped[float] = mapped_column(Float, nullable=True)
    p75_seconds: Mapped[float] = mapped_column(Float, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
