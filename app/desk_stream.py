from copy import deepcopy


# All other state, including quote timestamps, health and the event list, is
# sent on every heartbeat. These fields are always present in a full snapshot.
REUSABLE_FIELDS = ('structures', 'levels', 'opportunities', 'live_bars')


class StreamHistory:
    """Keep one detached copy per field. Each subscriber holds only version numbers."""

    def __init__(self):
        self.fields = {}
        self.version = 0

    def compact(self, state, previous):
        fields = {}
        versions = {}
        instruments = []
        for item in state['instruments']:
            row = item.copy()
            for name in REUSABLE_FIELDS:
                key = (item['root'], item['contract'], item['trading_day'], name)
                cached = self.fields.get(key)
                value = item[name]
                if cached is None or cached[1] != value:
                    # The engine also updates nested opportunities in place
                    # between bar closes, including touches and invalidations.
                    self.version += 1
                    cached = (self.version, deepcopy(value))
                fields[key] = cached
                versions[key] = cached[0]
                if previous.get(key) == cached[0]:
                    del row[name]
            instruments.append(row)
        self.fields = fields
        return {**state, 'instruments': instruments}, versions
