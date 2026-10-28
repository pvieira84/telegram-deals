"""Numeric five-field cron schedules, evaluated in the configured timezone."""
from datetime import datetime, timedelta


def field(text, low, high):
    values = set()
    for item in text.split(','):
        bits = item.split('/')
        if len(bits) > 2 or not bits[0]:
            raise ValueError('Invalid cron field')
        step = int(bits[1]) if len(bits)==2 else 1
        if step < 1:
            raise ValueError('Cron step must be positive')
        base = bits[0]
        if base == '*':
            start,end = low,high
        elif '-' in base:
            ends = base.split('-')
            if len(ends)!=2:
                raise ValueError('Invalid cron range')
            start,end = map(int,ends)
        else:
            start = int(base)
            end = high if len(bits)==2 else start
        if not low <= start <= end <= high:
            raise ValueError('Cron value outside allowed range')
        values.update(range(start,end+1,step))
    return values


class Cron:
    def __init__(self, expression):
        parts = expression.split()
        if len(parts)!=5:
            raise ValueError('digest_cron requires five fields: minute hour day month weekday')
        self.parts = parts
        self.fields = [field(p,*bounds) for p,bounds in zip(parts,[(0,59),(0,23),(1,31),(1,12),(0,7)])]
        if 7 in self.fields[4]:
            self.fields[4].add(0)

    def matches(self, dt):
        minute,hour,day,month,weekday = self.fields
        dom = dt.day in day
        dow = (dt.weekday()+1)%7 in weekday
        if self.parts[2] == '*' or self.parts[4] == '*':
            date_matches = dom and dow
        else:
            date_matches = dom or dow
        return dt.minute in minute and dt.hour in hour and dt.month in month and date_matches


class DigestSchedule:
    def __init__(self, cfg):
        expressions = cfg.get('digest_cron')
        self.crons = None
        self.times = None
        if expressions is not None:
            if isinstance(expressions,str):
                expressions = [expressions]
            if not isinstance(expressions,list) or not expressions or not all(isinstance(x,str) for x in expressions):
                raise ValueError('digest_cron must be a cron string or nonempty list of strings')
            self.crons = [Cron(x) for x in expressions]
        else:
            self.times = cfg.get('digest_times',['12:30','20:30'])
            if not isinstance(self.times,list):
                raise ValueError('digest_times must be a list')
            for t in self.times:
                if not isinstance(t,str) or datetime.strptime(t,'%H:%M').strftime('%H:%M') != t:
                    raise ValueError('digest_times must contain HH:MM times')

    def latest_due(self, now):
        if now.tzinfo is None:
            raise ValueError('Schedule requires a timezone-aware datetime')
        if self.times is not None:
            due = [t for t in self.times if t <= now.strftime('%H:%M')]
            return now.strftime('%Y-%m-%d')+' '+max(due) if due else None
        # Scan real instants so missing spring-forward times never run. Repeated
        # autumn wall times share one slot ID and therefore run only once.
        stamp = int(now.timestamp())//60*60
        for offset in range(26*60):
            candidate = datetime.fromtimestamp(stamp-offset*60,now.tzinfo)
            if candidate.date()!=now.date():
                break
            if any(c.matches(candidate) for c in self.crons):
                return candidate.strftime('%Y-%m-%d %H:%M')
        return None
