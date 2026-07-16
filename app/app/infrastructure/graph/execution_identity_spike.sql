pragma foreign_keys = on;

create table execution_sessions (
    id text primary key check (id like 'session_%'),
    owner_actor_id text not null
);

create table execution_threads (
    id text primary key check (id like 'thread_%'),
    session_id text not null references execution_sessions(id),
    graph_name text not null,
    graph_version text not null,
    parent_thread_id text null references execution_threads(id),
    unique (id, session_id)
);

create table execution_runs (
    id text primary key check (id like 'run_%'),
    session_id text not null,
    thread_id text not null,
    root_invocation_id text null check (
        root_invocation_id is null or root_invocation_id like 'invocation_%'
    ),
    status text not null check (
        status in (
            'created', 'running', 'waiting', 'retry_pending',
            'completed', 'failed', 'cancelled'
        )
    ),
    version integer not null default 0 check (version >= 0),
    foreign key (thread_id, session_id)
        references execution_threads(id, session_id),
    unique (id, thread_id),
    unique (id, session_id)
);

create unique index uq_execution_runs_active_thread
    on execution_runs(thread_id)
    where status in ('running', 'waiting', 'retry_pending');

create table run_attempts (
    id text primary key check (id like 'attempt_%'),
    run_id text not null references execution_runs(id),
    ordinal integer not null check (ordinal >= 1),
    reason text not null check (reason in ('initial', 'resume', 'retry')),
    worker_id text not null,
    status text not null check (
        status in ('running', 'waiting', 'succeeded', 'failed')
    ),
    start_checkpoint_id text null,
    end_checkpoint_id text null,
    unique (run_id, ordinal),
    unique (id, run_id)
);

create unique index uq_run_attempts_one_running
    on run_attempts(run_id)
    where status = 'running';

create table agent_invocations (
    id text primary key check (id like 'invocation_%'),
    run_id text not null references execution_runs(id),
    root_invocation_id text not null,
    parent_invocation_id text null,
    created_attempt_id text null,
    agent_profile_key text not null,
    status text not null check (
        status in ('planned', 'running', 'waiting', 'completed', 'failed')
    ),
    unique (id, run_id),
    foreign key (root_invocation_id, run_id)
        references agent_invocations(id, run_id)
        deferrable initially deferred,
    foreign key (parent_invocation_id, run_id)
        references agent_invocations(id, run_id),
    foreign key (created_attempt_id, run_id)
        references run_attempts(id, run_id)
);

create table checkpoint_bindings (
    thread_id text primary key references execution_threads(id),
    checkpoint_ns text not null,
    checkpoint_id text not null,
    graph_version text not null,
    unique (thread_id, checkpoint_ns, checkpoint_id)
);
