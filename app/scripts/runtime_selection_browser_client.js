const terminalTypes = new Set([
  "TimelineRunCompleted",
  "TimelineRunFailed",
  "TimelineRunCancelled",
]);

class RuntimeCursorClient {
  constructor({ streamUrl, eventsUrl, onState }) {
    this.streamUrl = streamUrl;
    this.eventsUrl = eventsUrl;
    this.onState = onState;
    this.lastEventId = null;
    this.events = [];
    this.seen = new Set();
    this.source = null;
    this.terminal = false;
  }

  connect() {
    const url = new URL(this.streamUrl, window.location.origin);
    if (this.lastEventId) {
      url.searchParams.set("last_event_id", this.lastEventId);
    }
    this.source = new EventSource(url);
    this.source.onmessage = (message) => {
      this.apply(JSON.parse(message.data));
    };
    this.source.onerror = async () => {
      this.source?.close();
      this.source = null;
      try {
        await this.reconcile();
        if (!this.terminal) {
          window.setTimeout(() => this.connect(), 25);
        }
      } catch (error) {
        this.onState({
          status: "failed",
          error: error instanceof Error ? error.message : String(error),
          eventIds: this.events.map((event) => event.id),
          eventTypes: this.events.map((event) => event.type),
        });
      }
    };
  }

  async reconcile() {
    const url = new URL(this.eventsUrl, window.location.origin);
    if (this.lastEventId) {
      url.searchParams.set("after_event_id", this.lastEventId);
    }
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(`snapshot failed with HTTP ${response.status}`);
    }
    const payload = await response.json();
    for (const event of payload.events) {
      this.apply(event);
    }
  }

  apply(event) {
    if (!event?.id || this.seen.has(event.id)) {
      return;
    }
    this.seen.add(event.id);
    this.events.push(event);
    this.lastEventId = event.id;
    this.terminal = terminalTypes.has(event.type);
    this.onState({
      status: this.terminal ? "completed" : "running",
      lastEventId: this.lastEventId,
      eventIds: this.events.map((item) => item.id),
      eventTypes: this.events.map((item) => item.type),
    });
  }
}

const output = document.querySelector("#state");
const client = new RuntimeCursorClient({
  streamUrl: "/stream",
  eventsUrl: "/events",
  onState(state) {
    output.textContent = JSON.stringify(state);
    if (state.status === "completed") {
      document.body.dataset.runtimeHarness = "passed";
    }
    if (state.status === "failed") {
      document.body.dataset.runtimeHarness = "failed";
    }
  },
});
client.connect();
