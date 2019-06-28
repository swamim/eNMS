from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
from sqlalchemy import (
    Boolean,
    case,
    Column,
    ForeignKey,
    Integer,
    PickleType,
    String,
    Text,
)
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import relationship
from typing import Any, List, Optional, Set, Tuple

from eNMS.concurrency import threaded_job
from eNMS.controller import controller
from eNMS.database import Session, SMALL_STRING_LENGTH, LARGE_STRING_LENGTH
from eNMS.database.associations import (
    job_event_table,
    event_syslog_table,
    task_device_table,
    task_pool_table,
)
from eNMS.database.base import AbstractBase


class Task(AbstractBase):

    __tablename__ = type = "Task"
    id = Column(Integer, primary_key=True)
    aps_job_id = Column(String(SMALL_STRING_LENGTH))
    name = Column(String(SMALL_STRING_LENGTH), unique=True)
    description = Column(String(SMALL_STRING_LENGTH))
    creation_time = Column(String(SMALL_STRING_LENGTH))
    scheduling_mode = Column(String(SMALL_STRING_LENGTH), default="standard")
    periodic = Column(Boolean)
    frequency = Column(Integer)
    frequency_unit = Column(String(SMALL_STRING_LENGTH), default="seconds")
    start_date = Column(String(SMALL_STRING_LENGTH))
    end_date = Column(String(SMALL_STRING_LENGTH))
    crontab_expression = Column(String(SMALL_STRING_LENGTH))
    is_active = Column(Boolean, default=False)
    payload = Column(MutableDict.as_mutable(PickleType), default={})
    devices = relationship(
        "Device", secondary=task_device_table, back_populates="tasks"
    )
    pools = relationship("Pool", secondary=task_pool_table, back_populates="tasks")
    job_id = Column(Integer, ForeignKey("Job.id"))
    job = relationship("Job", back_populates="tasks")
    job_name = association_proxy("job", "name")

    def __init__(self, **kwargs: Any) -> None:
        super().update(**kwargs)
        self.creation_time = controller.get_time()
        self.aps_job_id = kwargs.get("aps_job_id", self.creation_time)
        if self.is_active:
            self.schedule()

    def update(self, **kwargs: Any) -> None:
        super().update(**kwargs)
        if self.is_active:
            self.schedule()

    def generate_row(self, table: str) -> List[str]:
        status = "Pause" if self.is_active else "Resume"
        return [
            f"""<button id="pause-resume-{self.id}" type="button"
            class="btn btn-success btn-xs" onclick=
            "{status.lower()}Task('{self.id}')">{status}</button>""",
            f"""<button type="button" class="btn btn-primary btn-xs"
            onclick="showTypePanel('task', '{self.id}')">Edit</button>""",
            f"""<button type="button" class="btn btn-primary btn-xs"
            onclick="showTypePanel('task', '{self.id}', true)">
            Duplicate</button>""",
            f"""<button type="button" class="btn btn-danger btn-xs"
            onclick="showDeletionPanel('task', '{self.id}', '{self.name}')">
            Delete</button>""",
        ]

    @hybrid_property
    def status(self) -> str:
        return "Active" if self.is_active else "Inactive"

    @status.expression  # type: ignore
    def status(cls) -> str:  # noqa: N805
        return case([(cls.is_active, "Active")], else_="Inactive")

    @property
    def next_run_time(self) -> Optional[str]:
        job = controller.scheduler.get_job(self.aps_job_id)
        if job and job.next_run_time:
            return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
        return None

    @property
    def time_before_next_run(self) -> Optional[str]:
        job = controller.scheduler.get_job(self.aps_job_id)
        if job and job.next_run_time:
            delta = job.next_run_time.replace(tzinfo=None) - datetime.now()
            hours, remainder = divmod(delta.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            days = f"{delta.days} days, " if delta.days else ""
            return f"{days}{hours}h:{minutes}m:{seconds}s"
        return None

    def aps_conversion(self, date: str) -> str:
        dt: datetime = datetime.strptime(date, "%d/%m/%Y %H:%M:%S")
        return datetime.strftime(dt, "%Y-%m-%d %H:%M:%S")

    def aps_date(self, datetype: str) -> Optional[str]:
        date = getattr(self, datetype)
        return self.aps_conversion(date) if date else None

    def pause(self) -> None:
        controller.scheduler.pause_job(self.aps_job_id)
        self.is_active = False
        Session.commit()

    def resume(self) -> None:
        self.schedule()
        controller.scheduler.resume_job(self.aps_job_id)
        self.is_active = True
        Session.commit()

    def delete_task(self) -> None:
        if controller.scheduler.get_job(self.aps_job_id):
            controller.scheduler.remove_job(self.aps_job_id)
        Session.commit()

    def compute_targets(self) -> Set[int]:
        targets = {device.id for device in self.devices}
        for pool in self.pools:
            targets |= {device.id for device in pool.devices}
        return targets

    def kwargs(self) -> Tuple[dict, dict]:
        default = {
            "id": self.aps_job_id,
            "func": threaded_job,
            "replace_existing": True,
            "args": [
                self.job.id,
                self.aps_job_id,
                self.compute_targets(),
                self.payload,
            ],
        }
        if self.scheduling_mode == "cron":
            self.periodic = True
            trigger = {"trigger": CronTrigger.from_crontab(self.crontab_expression)}
        elif self.frequency:
            self.periodic = True
            frequency_in_seconds = (
                int(self.frequency)
                * {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}[
                    self.frequency_unit
                ]
            )
            trigger = {
                "trigger": "interval",
                "start_date": self.aps_date("start_date"),
                "end_date": self.aps_date("end_date"),
                "seconds": frequency_in_seconds,
            }
        else:
            self.periodic = False
            trigger = {"trigger": "date", "run_date": self.aps_date("start_date")}
        return default, trigger

    def schedule(self) -> None:
        default, trigger = self.kwargs()
        if not controller.scheduler.get_job(self.aps_job_id):
            controller.scheduler.add_job(**{**default, **trigger})
        else:
            controller.scheduler.reschedule_job(default.pop("id"), **trigger)


class Baselog(AbstractBase):

    __tablename__ = "Baselog"
    type = Column(String(SMALL_STRING_LENGTH), default="")
    __mapper_args__ = {"polymorphic_identity": "Baselog", "polymorphic_on": type}
    id = Column(Integer, primary_key=True)
    time = Column(String(SMALL_STRING_LENGTH), default="")
    content = Column(Text(LARGE_STRING_LENGTH), default="")

    def update(self, **kwargs: str) -> None:
        kwargs["time"] = str(datetime.now())
        super().update(**kwargs)

    def generate_row(self, table: str) -> List[str]:
        return []


class Syslog(Baselog):

    __tablename__ = "Syslog"
    __mapper_args__ = {"polymorphic_identity": "Syslog"}
    parent_cls = "Baselog"
    id = Column(Integer, ForeignKey("Baselog.id"), primary_key=True)
    source = Column(Text(LARGE_STRING_LENGTH), default="")
    events = relationship(
        "Event", secondary=event_syslog_table, back_populates="syslogs"
    )


class Changelog(Baselog):

    __tablename__ = "Changelog"
    __mapper_args__ = {"polymorphic_identity": "Changelog"}
    parent_cls = "Baselog"
    id = Column(Integer, ForeignKey("Baselog.id"), primary_key=True)
    severity = Column(String(SMALL_STRING_LENGTH), default="N/A")
    user = Column(Text(LARGE_STRING_LENGTH), default="")


class Event(AbstractBase):

    __tablename__ = type = "Event"
    id = Column(Integer, primary_key=True)
    name = Column(String(SMALL_STRING_LENGTH), unique=True)
    origin = Column(String(SMALL_STRING_LENGTH), default="")
    origin_regex = Column(Boolean, default=False)
    name = Column(String(SMALL_STRING_LENGTH), default="")
    content_regex = Column(Boolean, default=False)
    syslogs = relationship(
        "Syslog", secondary=event_syslog_table, back_populates="events"
    )
    jobs = relationship("Job", secondary=job_event_table, back_populates="events")

    def generate_row(self, table: str) -> List[str]:
        return [
            f"""<button type="button" class="btn btn-info btn-xs"
            onclick="showTypePanel('event', '{self.id}')">
            Edit</button>""",
            f"""<button type="button" class="btn btn-danger btn-xs"
            onclick="deleteInstance('event', '{self.id}', '{self.name}')">
            Delete</button>""",
        ]