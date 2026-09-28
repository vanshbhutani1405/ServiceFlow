from agent.routing import SchedulingIntent, route_request


def test_routes_scheduling_intents_without_an_llm():
    assert route_request("I need someone tomorrow at 2") == SchedulingIntent.BOOK
    assert route_request("move my appointment to Friday") == SchedulingIntent.RESCHEDULE
    assert route_request("please cancel my appointment") == SchedulingIntent.CANCEL
    assert route_request("what appointment times are available?") == SchedulingIntent.GENERAL_SCHEDULING
    assert route_request("the technician was very helpful") == SchedulingIntent.UNKNOWN

