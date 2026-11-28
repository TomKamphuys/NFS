"""Driver fault detection shared by live acquisition and reference validation."""

ERROR_FLAGS = ('input_underflow', 'input_overflow', 'output_underflow', 'output_overflow')


def stream_fault(events):
    """Describe the first faulty callback, including older text-only recordings."""
    for event in events:
        flags = [flag for flag in ERROR_FLAGS if event.get(flag)]
        status = str(event.get('status', ''))
        if flags or 'underflow' in status.casefold() or 'overflow' in status.casefold():
            description = ', '.join(flags) if flags else status
            sample = event.get('sample')
            location = f' at sample {sample}' if sample is not None else ''
            return f'stream health rejection: {description}{location}'
    return None
