Feature: Ad delivery anomaly detection
  As an ad operations engineer
  I want silent failures surfaced as incidents
  So that money is not lost while every dashboard stays green

  Scenario: A tracking blackout is critical
    Given a campaign with two weeks of steady history
    When impressions fall to zero on the latest day
    Then the detector reports "TRACKING_DARK" as critical

  Scenario: A steady campaign produces no incidents
    Given a campaign with two weeks of steady history
    When the latest day matches the baseline
    Then no incident is reported

  Scenario: A missing day is its own incident
    Given a campaign with two weeks of steady history
    When the latest day has no metrics row
    Then the detector reports "NO_DATA" as critical

  Scenario: A spend spike is a warning
    Given a campaign with two weeks of steady history
    When spend triples on the latest day
    Then the detector reports a "SPEND_SPIKE" warning
