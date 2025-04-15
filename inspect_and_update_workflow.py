#!/usr/bin/env python3
import json
"""
  <table id="workflow-schedule-table" border="1">
   <thead>
    <tr>
     <th>Month</th>
     <th>Draft</th>
     <th>Open</th>
     <th>In Progress</th>
     <th>Ignored<sup>1</sup></th>
     <th>Releases</th>
    </tr>
   </thead>
   <tbody>
    <tr>
     <th>January</th>
     <td>Drafts PG18</td>
     <td>2025-03</td>
     <td>2025-01</td>
     <td>&lt;=2024</td>
     <td></td>
    </tr>
    <tr>
     <th>February</th>
     <td>"</td>
     <td>"</td>
     <td>None</td>
     <td>2025-01</td>
     <td>PG17.2</td>
    </tr>
    <tr>
     <th>March<sup>2</sup></th>
     <td>Drafts PG19</td>
     <td>2025-07</td>
     <td>2025-03</td>
     <td>Drafts PG18<sup>3</sup>, 2025-01</td>
     <td></td>
    </tr>
    <tr>
     <th>April</th>
     <td>"</td>
     <td>"</td>
     <td>None</td>
     <td>2025-01, 2025-03</td>
     <td></td>
    </tr>
    <tr>
     <th>May</th>
     <td>"</td>
     <td>"</td>
     <td>None</td>
     <td>2025-03</td>
     <td>PG17.3</td>
    </tr>
    <tr>
     <th>June</th>
     <td>"</td>
     <td>"</td>
     <td>None</td>
     <td>2025-03</td>
     <td></td>
    </tr>
    <tr>
     <th>July</th>
     <td>"</td>
     <td>2025-09</td>
     <td>2025-07</td>
     <td>2025-03</td>
     <td></td>
    </tr>
    <tr>
     <th>August</th>
     <td>"</td>
     <td>"</td>
     <td>None</td>
     <td>2025-03, 2025-07</td>
     <td>PG17.4</td>
    </tr>
    <tr>
     <th>September</th>
     <td>"</td>
     <td>2025-11</td>
     <td>2025-09</td>
     <td>2025-07</td>
     <td>PG18.0<sup>4</sup></td>
    </tr>
    <tr>
     <th>October</th>
     <td>"</td>
     <td>"</td>
     <td>None</td>
     <td>2025-07, 2025-09</td>
     <td></td>
    </tr>
    <tr>
     <th>November</th>
     <td>"</td>
     <td>2026-01</td>
     <td>2025-11</td>
     <td>2025-09</td>
     <td>PG18.1, Final PG13.23</td>
    </tr>
    <tr>
     <th>December</th>
     <td>"</td>
     <td>"</td>
     <td>None</td>
     <td>2025-09, 2025-11</td>
     <td></td>
    </tr>
   </tbody>
  </table>
"""

"""
The goal of this script is, given a date, use the month to determine the expected
workflow state on a month-by-month basis for the open, in progress, and draft
commitfests.  Then figure out the current month and prior month from the date.
Determine the existing workflow state.  If the existing state matches the current
month do nothing.  If it matches the prior month perform actions to make it match
the model of the current month.  If if matches neither abort.  Use the schedule
in the table above and the fact it is for the year 2025 to do this.
"""

import datetime

def parse_schedule_table(year):
    """
    Dynamically build the workflow schedule table based on the given year.
    """
    schedule = {
        "January": {"draft": f"Drafts PG{year - 2007}", "open": f"{year}-03", "in_progress": f"{year}-01"},
        "February": {"draft": f"Drafts PG{year - 2007}", "open": f"{year}-03", "in_progress": None},
        "March": {"draft": f"Drafts PG{year - 2006}", "open": f"{year}-07", "in_progress": f"{year}-03"},
        "April": {"draft": f"Drafts PG{year - 2006}", "open": f"{year}-07", "in_progress": None},
        "May": {"draft": f"Drafts PG{year - 2006}", "open": f"{year}-07", "in_progress": None},
        "June": {"draft": f"Drafts PG{year - 2006}", "open": f"{year}-07", "in_progress": None},
        "July": {"draft": f"Drafts PG{year - 2006}", "open": f"{year}-09", "in_progress": f"{year}-07"},
        "August": {"draft": f"Drafts PG{year - 2006}", "open": f"{year}-09", "in_progress": None},
        "September": {"draft": f"Drafts PG{year - 2006}", "open": f"{year}-11", "in_progress": f"{year}-09"},
        "October": {"draft": f"Drafts PG{year - 2006}", "open": f"{year}-11", "in_progress": None},
        "November": {"draft": f"Drafts PG{year - 2006}", "open": f"{year + 1}-01", "in_progress": f"{year}-11"},
        "December": {"draft": f"Drafts PG{year - 2006}", "open": f"{year + 1}-01", "in_progress": None},
    }
    return schedule

def get_month_states(date):
    """
    Given a date, determine the current and prior month's workflow states.
    """
    year = date.year
    schedule = parse_schedule_table(year)
    current_month = date.strftime("%B")
    prior_month = (date - datetime.timedelta(days=30)).strftime("%B")
    return schedule.get(current_month), schedule.get(prior_month)

def test_workflow_state(current_state, date):
    """
    Check the workflow state based on the given date and return the matching result as a tuple.
    """
    current_month_state, prior_month_state = get_month_states(date)

    if current_state == current_month_state:
        return current_month_state, "Workflow state matches the current month. No action needed."
    elif current_state == prior_month_state:
        # If current and prior month states are the same, prioritize the current month match
        if current_month_state == prior_month_state:
            return current_month_state, "Workflow state matches the current month. No action needed."
        return current_month_state, "Workflow state matches the prior month."
    else:
        return current_month_state, "Workflow state does not match the current or prior month."

def describe_changes(current_state, current_month_state):
    """
    Describe the changes needed to align the current state with the current month's state.
    Reverse 'name' and 'action' so that the values in current_state become the keys in the changes array objects.
    """
    changes = []
    all_values = set(current_state.values()).union(current_month_state.values())
    for value in all_values:
        if value is None: continue
        state_was = next((k for k, v in current_state.items() if v == value), None)
        state_become = next((k for k, v in current_month_state.items() if v == value), None)
        if state_was and state_become and state_was != state_become:
            # moved within the workflow - just needs a status update to the new one
            changes.append({"action": state_become, "name": value})
        elif state_was == state_become:
            # no change needed
            changes.append({"action": "No Change", "name": value})
        elif state_was and not state_become:
            # moved out of the workflow - needs to be closed
            changes.append({"action": "Closed", "name": value})
        elif not state_was and state_become:
            # moved into the workflow - needs to be opened
            changes.append({"action": state_become, "name": value})
        else:
            # this should not happen
            changes.append({"action": "Error", "name": f"Unexpected state: {state_was} vs {state_become} for {value}"})

    return changes

def test_update_workflow_state():
    """
    Test cases for test_workflow_state function.
    """
    test_cases = [
        # February: Matches the scheduled month
        {
            "date": datetime.date(2025, 2, 15),
            "current_state": {"draft": "Drafts PG18", "open": "2025-03", "in_progress": None},
            "expected": "Workflow state matches the current month. No action needed."
        },
        # February: Matches the prior month (January)
        {
            "date": datetime.date(2025, 2, 15),
            "current_state": {"draft": "Drafts PG18", "open": "2025-03", "in_progress": "2025-01"},
            "expected": "Workflow state matches the prior month."
        },
        # March: Matches the scheduled month
        {
            "date": datetime.date(2025, 3, 15),
            "current_state": {"draft": "Drafts PG19", "open": "2025-07", "in_progress": "2025-03"},
            "expected": "Workflow state matches the current month. No action needed."
        },
        # March: Matches the prior month (February)
        {
            "date": datetime.date(2025, 3, 15),
            "current_state": {"draft": "Drafts PG18", "open": "2025-03", "in_progress": None},
            "expected": "Workflow state matches the prior month."
        },
        # May: Matches the scheduled month
        {
            "date": datetime.date(2025, 5, 15),
            "current_state": {"draft": "Drafts PG19", "open": "2025-07", "in_progress": None},
            "expected": "Workflow state matches the current month. No action needed."
        },
        # May: Matches the prior month (April) but shares the same schedule
        {
            "date": datetime.date(2025, 5, 15),
            "current_state": {"draft": "Drafts PG19", "open": "2025-07", "in_progress": None},
            "expected": "Workflow state matches the current month. No action needed."
        },
    ]

    for i, case in enumerate(test_cases, 1):
        print(f"Running test case {i}...")
        date = case["date"]
        current_state = case["current_state"]
        expected = case["expected"]

        # Call test_workflow_state and compare the result
        current_month_state, actual_output = test_workflow_state(current_state, date)

        # Print details for cases where the match is due to the prior month
        if actual_output == "Workflow state matches the prior month.":
            print(f"Details for test case {i}:")
            print(f"  Current Month State: {current_month_state}")
            print(f"  Current State: {current_state}")
            changes = describe_changes(current_state, current_month_state)
            print(f"  Change Summary: \n" + json.dumps(changes, indent=2))

        assert actual_output == expected, f"Test case {i} failed: Expected '{expected}', got '{actual_output}'"

    print("All test cases passed!")

# Example usage
if __name__ == "__main__":
    given_date = datetime.date(2025, 3, 15)  # Example date
    test_update_workflow_state()
