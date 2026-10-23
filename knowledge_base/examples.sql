-- Example 1
-- Question: How many patients are registered?
SELECT COUNT(*) AS total_patients
FROM patients;

-- Example 2
-- Question: How many patients are there for each recorded gender?
SELECT gender, COUNT(*) AS patient_count
FROM patients
GROUP BY gender
ORDER BY gender;

-- Example 3
-- Question: List all patients with their full name and phone number, sorted by last name.
SELECT patient_id, CONCAT(first_name, ' ', last_name) AS patient_name, contact_number
FROM patients
ORDER BY last_name, first_name, patient_id;

-- Example 4
-- Question: Which patients registered during 2022?
SELECT patient_id, first_name, last_name, registration_date
FROM patients
WHERE registration_date >= '2022-01-01'
  AND registration_date < '2023-01-01'
ORDER BY registration_date, patient_id;

-- Example 5
-- Question: How many patients registered in each calendar year that has registrations?
SELECT YEAR(registration_date) AS registration_year, COUNT(*) AS patient_count
FROM patients
GROUP BY YEAR(registration_date)
ORDER BY registration_year;

-- Example 6
-- Question: Show the five oldest patients, using patient ID to break ties.
SELECT patient_id, first_name, last_name, date_of_birth
FROM patients
ORDER BY date_of_birth, patient_id
LIMIT 5;

-- Example 7
-- Question: Which patients were born before 1960?
SELECT patient_id, first_name, last_name, date_of_birth
FROM patients
WHERE date_of_birth < '1960-01-01'
ORDER BY date_of_birth, patient_id;

-- Example 8
-- Question: How many patients are currently at least 60 years old?
SELECT COUNT(*) AS patient_count
FROM patients
WHERE TIMESTAMPDIFF(YEAR, date_of_birth, CURDATE()) >= 60;

-- Example 9
-- Question: What is each patient's current age in completed years?
SELECT patient_id, first_name, last_name,
       TIMESTAMPDIFF(YEAR, date_of_birth, CURDATE()) AS age_years
FROM patients
ORDER BY age_years DESC, patient_id;

-- Example 10
-- Question: How many patients are currently under 30, aged 30 to 49, aged 50 to 69, or aged 70 and above?
SELECT CASE
           WHEN TIMESTAMPDIFF(YEAR, date_of_birth, CURDATE()) < 30 THEN 'Under 30'
           WHEN TIMESTAMPDIFF(YEAR, date_of_birth, CURDATE()) < 50 THEN '30-49'
           WHEN TIMESTAMPDIFF(YEAR, date_of_birth, CURDATE()) < 70 THEN '50-69'
           ELSE '70 and above'
       END AS age_group,
       COUNT(*) AS patient_count
FROM patients
GROUP BY age_group
ORDER BY MIN(TIMESTAMPDIFF(YEAR, date_of_birth, CURDATE()));

-- Example 11
-- Question: Which nonblank email addresses are shared by more than one patient, ignoring surrounding spaces and letter case?
SELECT LOWER(TRIM(email)) AS normalized_email, COUNT(*) AS patient_count
FROM patients
WHERE email IS NOT NULL
  AND TRIM(email) <> ''
GROUP BY LOWER(TRIM(email))
HAVING COUNT(*) > 1
ORDER BY patient_count DESC, normalized_email;

-- Example 12
-- Question: List every patient whose last name is Smith.
SELECT patient_id, first_name, last_name, contact_number
FROM patients
WHERE last_name = 'Smith'
ORDER BY first_name, patient_id;

-- Example 13
-- Question: Which patients have an address containing Maple Dr?
SELECT patient_id, first_name, last_name, address
FROM patients
WHERE address LIKE '%Maple Dr%'
ORDER BY patient_id;

-- Example 14
-- Question: Which patients have no insurance number recorded, treating NULL and blank text as missing?
SELECT patient_id, first_name, last_name, insurance_provider
FROM patients
WHERE insurance_number IS NULL
   OR TRIM(insurance_number) = ''
ORDER BY patient_id;

-- Example 15
-- Question: What are the earliest and latest patient registration dates?
SELECT MIN(registration_date) AS first_registration,
       MAX(registration_date) AS last_registration
FROM patients;

-- Example 16
-- Question: How many distinct nonblank insurance providers are recorded, ignoring surrounding spaces and letter case?
SELECT COUNT(DISTINCT LOWER(TRIM(insurance_provider))) AS insurance_provider_count
FROM patients
WHERE insurance_provider IS NOT NULL
  AND TRIM(insurance_provider) <> '';

-- Example 17
-- Question: What is the average current age in completed years for each gender, counting each patient once?
SELECT gender,
       ROUND(AVG(TIMESTAMPDIFF(YEAR, date_of_birth, CURDATE())), 1) AS average_age_years
FROM patients
GROUP BY gender
ORDER BY gender;

-- Example 18
-- Question: How many patients registered in each month of 2023 that has registrations?
SELECT DATE_FORMAT(registration_date, '%Y-%m') AS registration_month,
       COUNT(*) AS patient_count
FROM patients
WHERE registration_date >= '2023-01-01'
  AND registration_date < '2024-01-01'
GROUP BY DATE_FORMAT(registration_date, '%Y-%m')
ORDER BY registration_month;

-- Example 19
-- Question: How many patients have each nonblank insurance provider recorded?
SELECT TRIM(insurance_provider) AS provider, COUNT(*) AS patient_count
FROM patients
WHERE insurance_provider IS NOT NULL
  AND TRIM(insurance_provider) <> ''
GROUP BY TRIM(insurance_provider)
ORDER BY patient_count DESC, provider;

-- Example 20
-- Question: Which recorded nonblank insurance providers have fewer than 10 patients?
SELECT TRIM(insurance_provider) AS provider, COUNT(*) AS patient_count
FROM patients
WHERE insurance_provider IS NOT NULL
  AND TRIM(insurance_provider) <> ''
GROUP BY TRIM(insurance_provider)
HAVING COUNT(*) < 10
ORDER BY patient_count, provider;

-- Example 21
-- Question: Which patients are missing an email address?
SELECT patient_id, first_name, last_name
FROM patients
WHERE email IS NULL OR TRIM(email) = ''
ORDER BY patient_id;

-- Example 22
-- Question: How many patients have an insurance number recorded and how many do not, treating blank text as missing?
SELECT CASE
           WHEN insurance_number IS NULL OR TRIM(insurance_number) = ''
               THEN 'Not recorded'
           ELSE 'Recorded'
       END AS insurance_number_status,
       COUNT(*) AS patient_count
FROM patients
GROUP BY insurance_number_status
ORDER BY insurance_number_status;

-- Example 23
-- Question: Show each patient's insurance provider, displaying Not recorded for NULL or blank text.
SELECT patient_id, first_name, last_name,
       CASE
           WHEN insurance_provider IS NULL OR TRIM(insurance_provider) = ''
               THEN 'Not recorded'
           ELSE insurance_provider
       END AS insurance_provider
FROM patients
ORDER BY patient_id;

-- Example 24
-- Question: How many patients were born in each month of the year that has births recorded?
SELECT MONTH(date_of_birth) AS birth_month_number,
       DATE_FORMAT(date_of_birth, '%M') AS birth_month,
       COUNT(*) AS patient_count
FROM patients
GROUP BY MONTH(date_of_birth), DATE_FORMAT(date_of_birth, '%M')
ORDER BY birth_month_number;

-- Example 25
-- Question: Which patients were born during the 1980s?
SELECT patient_id, first_name, last_name, date_of_birth
FROM patients
WHERE date_of_birth >= '1980-01-01'
  AND date_of_birth < '1990-01-01'
ORDER BY date_of_birth, patient_id;

-- Example 26
-- Question: How many doctors are registered?
SELECT COUNT(*) AS doctor_count
FROM doctors;

-- Example 27
-- Question: How many doctors are there in each specialization?
SELECT specialization, COUNT(*) AS doctor_count
FROM doctors
GROUP BY specialization
ORDER BY specialization;

-- Example 28
-- Question: How many doctors work at each hospital branch?
SELECT hospital_branch, COUNT(*) AS doctor_count
FROM doctors
GROUP BY hospital_branch
ORDER BY hospital_branch;

-- Example 29
-- Question: Which doctors have more than 20 years of experience?
SELECT doctor_id, first_name, last_name, specialization, years_experience
FROM doctors
WHERE years_experience > 20
ORDER BY years_experience DESC, doctor_id;

-- Example 30
-- Question: What is the average years of experience in each specialization?
SELECT specialization, ROUND(AVG(years_experience), 1) AS average_years_experience
FROM doctors
GROUP BY specialization
ORDER BY specialization;

-- Example 31
-- Question: Show the three most experienced doctors, using doctor ID to break ties.
SELECT doctor_id, first_name, last_name, years_experience
FROM doctors
ORDER BY years_experience DESC, doctor_id
LIMIT 3;

-- Example 32
-- Question: Which doctors work at Central Hospital?
SELECT doctor_id, first_name, last_name, specialization
FROM doctors
WHERE hospital_branch = 'Central Hospital'
ORDER BY last_name, first_name, doctor_id;

-- Example 33
-- Question: How many doctors of each specialization work at each hospital branch?
SELECT hospital_branch, specialization, COUNT(*) AS doctor_count
FROM doctors
GROUP BY hospital_branch, specialization
ORDER BY hospital_branch, specialization;

-- Example 34
-- Question: Which hospital branches have doctors from more than one specialization?
SELECT hospital_branch, COUNT(DISTINCT specialization) AS specialization_count
FROM doctors
GROUP BY hospital_branch
HAVING COUNT(DISTINCT specialization) > 1
ORDER BY hospital_branch;

-- Example 35
-- Question: What are the minimum and maximum years of doctor experience at each branch?
SELECT hospital_branch, MIN(years_experience) AS minimum_years,
       MAX(years_experience) AS maximum_years
FROM doctors
GROUP BY hospital_branch
ORDER BY hospital_branch;

-- Example 36
-- Question: Which specializations are recorded?
SELECT DISTINCT specialization
FROM doctors
ORDER BY specialization;

-- Example 37
-- Question: Which doctors have between 10 and 20 years of experience, inclusive?
SELECT doctor_id, first_name, last_name, years_experience
FROM doctors
WHERE years_experience BETWEEN 10 AND 20
ORDER BY years_experience, doctor_id;

-- Example 38
-- Question: List doctors specializing in Pediatrics or Oncology.
SELECT doctor_id, first_name, last_name, specialization, hospital_branch
FROM doctors
WHERE specialization IN ('Pediatrics', 'Oncology')
ORDER BY specialization, last_name, doctor_id;

-- Example 39
-- Question: How many doctors have under 10 years, 10 to 19 years, or at least 20 years of experience?
SELECT CASE
           WHEN years_experience < 10 THEN 'Under 10'
           WHEN years_experience < 20 THEN '10-19'
           ELSE '20 and above'
       END AS experience_band,
       COUNT(*) AS doctor_count
FROM doctors
GROUP BY experience_band
ORDER BY MIN(years_experience);

-- Example 40
-- Question: List all doctors whose first name is David so I can distinguish them.
SELECT doctor_id, first_name, last_name, specialization, hospital_branch
FROM doctors
WHERE first_name = 'David'
ORDER BY last_name, doctor_id;

-- Example 41
-- Question: How many appointments are there for each status?
SELECT status, COUNT(*) AS appointment_count
FROM appointments
GROUP BY status
ORDER BY status;

-- Example 42
-- Question: How many appointments are there for each reason for visit?
SELECT reason_for_visit, COUNT(*) AS appointment_count
FROM appointments
GROUP BY reason_for_visit
ORDER BY appointment_count DESC, reason_for_visit;

-- Example 43
-- Question: Which appointments are dated 15 June 2023?
SELECT appointment_id, patient_id, doctor_id, appointment_time, status
FROM appointments
WHERE appointment_date = '2023-06-15'
ORDER BY appointment_time, appointment_id;

-- Example 44
-- Question: How many appointments were cancelled or marked as no-shows?
SELECT COUNT(*) AS cancelled_or_no_show_count
FROM appointments
WHERE status IN ('Cancelled', 'No-show');

-- Example 45
-- Question: What percentage of all recorded appointments have status Completed?
SELECT ROUND(
           100.0 * SUM(CASE WHEN status = 'Completed' THEN 1 ELSE 0 END)
           / NULLIF(COUNT(*), 0), 1
       ) AS completed_percent
FROM appointments;

-- Example 46
-- Question: How many appointments start before noon compared with noon or later?
SELECT CASE
           WHEN appointment_time < '12:00:00' THEN 'Before noon'
           ELSE 'Noon or later'
       END AS time_of_day,
       COUNT(*) AS appointment_count
FROM appointments
GROUP BY time_of_day
ORDER BY time_of_day;

-- Example 47
-- Question: Which appointments start at 4 PM or later?
SELECT appointment_id, appointment_date, appointment_time, doctor_id, status
FROM appointments
WHERE appointment_time >= '16:00:00'
ORDER BY appointment_date, appointment_time, appointment_id;

-- Example 48
-- Question: How many appointments fall on each weekday that appears in the data, ordered Sunday through Saturday?
SELECT DATE_FORMAT(appointment_date, '%W') AS weekday,
       COUNT(*) AS appointment_count
FROM appointments
GROUP BY DATE_FORMAT(appointment_date, '%w'),
         DATE_FORMAT(appointment_date, '%W')
ORDER BY DATE_FORMAT(appointment_date, '%w');

-- Example 49
-- Question: How many appointments of each status are recorded in each month of 2023? Show existing month-status combinations only.
SELECT DATE_FORMAT(appointment_date, '%Y-%m') AS appointment_month,
       status, COUNT(*) AS appointment_count
FROM appointments
WHERE appointment_date >= '2023-01-01'
  AND appointment_date < '2024-01-01'
GROUP BY DATE_FORMAT(appointment_date, '%Y-%m'), status
ORDER BY appointment_month, status;

-- Example 50
-- Question: Which single date has the most recorded appointments? Use the earliest date to break ties.
SELECT appointment_date, COUNT(*) AS appointment_count
FROM appointments
GROUP BY appointment_date
ORDER BY appointment_count DESC, appointment_date
LIMIT 1;

-- Example 51
-- Question: How many appointments are recorded in each quarter of 2023 that has appointments?
SELECT CASE
           WHEN MONTH(appointment_date) <= 3 THEN 'Q1'
           WHEN MONTH(appointment_date) <= 6 THEN 'Q2'
           WHEN MONTH(appointment_date) <= 9 THEN 'Q3'
           ELSE 'Q4'
       END AS quarter,
       COUNT(*) AS appointment_count
FROM appointments
WHERE appointment_date >= '2023-01-01'
  AND appointment_date < '2024-01-01'
GROUP BY quarter
ORDER BY quarter;

-- Example 52
-- Question: How many appointments with reason Emergency have status Completed?
SELECT COUNT(*) AS completed_emergency_count
FROM appointments
WHERE reason_for_visit = 'Emergency'
  AND status = 'Completed';

-- Example 53
-- Question: For Emergency appointments, how many are recorded in each status?
SELECT status, COUNT(*) AS appointment_count
FROM appointments
WHERE reason_for_visit = 'Emergency'
GROUP BY status
ORDER BY status;

-- Example 54
-- Question: Which month of 2023 has the most appointments? Use the earliest month to break ties.
SELECT DATE_FORMAT(appointment_date, '%Y-%m') AS appointment_month,
       COUNT(*) AS appointment_count
FROM appointments
WHERE appointment_date >= '2023-01-01'
  AND appointment_date < '2024-01-01'
GROUP BY DATE_FORMAT(appointment_date, '%Y-%m')
ORDER BY appointment_count DESC, appointment_month
LIMIT 1;

-- Example 55
-- Question: Show the 10 most recent recorded appointments by their scheduled date and time.
SELECT appointment_id, appointment_date, appointment_time,
       patient_id, doctor_id, status
FROM appointments
ORDER BY appointment_date DESC, appointment_time DESC, appointment_id
LIMIT 10;

-- Example 56
-- Question: How many distinct patients have at least one appointment of any status?
SELECT COUNT(DISTINCT patient_id) AS patient_count
FROM appointments;

-- Example 57
-- Question: Among patients who have appointments, what is the average number of appointments per patient, including every status?
SELECT ROUND(
           COUNT(*) * 1.0 / NULLIF(COUNT(DISTINCT patient_id), 0), 2
       ) AS appointments_per_patient
FROM appointments;

-- Example 58
-- Question: How many appointments are recorded in the first and second halves of 2023? Show both totals even when zero.
SELECT COUNT(CASE
                 WHEN appointment_date >= '2023-01-01'
                  AND appointment_date < '2023-07-01' THEN 1
             END) AS first_half_count,
       COUNT(CASE
                 WHEN appointment_date >= '2023-07-01'
                  AND appointment_date < '2024-01-01' THEN 1
             END) AS second_half_count
FROM appointments;

-- Example 59
-- Question: For each reason for visit, how many appointments are recorded and how many are no-shows?
SELECT reason_for_visit, COUNT(*) AS appointment_count,
       SUM(CASE WHEN status = 'No-show' THEN 1 ELSE 0 END) AS no_show_count
FROM appointments
GROUP BY reason_for_visit
ORDER BY reason_for_visit;

-- Example 60
-- Question: What percentage of appointments are no-shows for each reason for visit?
SELECT reason_for_visit,
       ROUND(
           100.0 * SUM(CASE WHEN status = 'No-show' THEN 1 ELSE 0 END)
           / COUNT(*), 1
       ) AS no_show_percent
FROM appointments
GROUP BY reason_for_visit
ORDER BY no_show_percent DESC, reason_for_visit;

-- Example 61
-- Question: How many appointments fall on Friday or Saturday, using those two days as the weekend?
SELECT COUNT(*) AS weekend_appointment_count
FROM appointments
WHERE DATE_FORMAT(appointment_date, '%w') IN ('5', '6');

-- Example 62
-- Question: How many appointments were recorded in July 2023?
SELECT COUNT(*) AS appointment_count
FROM appointments
WHERE appointment_date >= '2023-07-01'
  AND appointment_date < '2023-08-01';

-- Example 63
-- Question: Which appointments are dated from 1 May 2023 through 7 May 2023, inclusive?
SELECT appointment_id, appointment_date, appointment_time, status
FROM appointments
WHERE appointment_date >= '2023-05-01'
  AND appointment_date < '2023-05-08'
ORDER BY appointment_date, appointment_time, appointment_id;

-- Example 64
-- Question: How many distinct patients have at least one cancelled appointment?
SELECT COUNT(DISTINCT patient_id) AS patient_count
FROM appointments
WHERE status = 'Cancelled';

-- Example 65
-- Question: How many appointments have status Scheduled and a date after 31 December 2023?
SELECT COUNT(*) AS scheduled_appointment_count
FROM appointments
WHERE status = 'Scheduled'
  AND appointment_date >= '2024-01-01';

-- Example 66
-- Question: How many treatment records are there for each treatment type?
SELECT treatment_type, COUNT(*) AS treatment_count
FROM treatments
GROUP BY treatment_type
ORDER BY treatment_type;

-- Example 67
-- Question: What is the total treatment cost for each treatment type?
SELECT treatment_type, SUM(cost) AS total_treatment_cost
FROM treatments
GROUP BY treatment_type
ORDER BY total_treatment_cost DESC, treatment_type;

-- Example 68
-- Question: What are the minimum, maximum and average treatment costs for each recorded description, including a separate NULL group?
SELECT description, MIN(cost) AS minimum_cost, MAX(cost) AS maximum_cost,
       ROUND(AVG(cost), 2) AS average_cost
FROM treatments
GROUP BY description
ORDER BY description;

-- Example 69
-- Question: Which treatments cost more than 4,500?
SELECT treatment_id, treatment_type, description, cost, treatment_date
FROM treatments
WHERE cost > 4500
ORDER BY cost DESC, treatment_id;

-- Example 70
-- Question: Show the five most expensive treatments, using treatment ID to break ties.
SELECT treatment_id, treatment_type, cost, treatment_date
FROM treatments
ORDER BY cost DESC, treatment_id
LIMIT 5;

-- Example 71
-- Question: What is the total treatment cost in each month of 2023 that has treatment records?
SELECT DATE_FORMAT(treatment_date, '%Y-%m') AS treatment_month,
       SUM(cost) AS total_treatment_cost
FROM treatments
WHERE treatment_date >= '2023-01-01'
  AND treatment_date < '2024-01-01'
GROUP BY DATE_FORMAT(treatment_date, '%Y-%m')
ORDER BY treatment_month;

-- Example 72
-- Question: How many treatments are recorded for each treatment type and description combination?
SELECT treatment_type, description, COUNT(*) AS treatment_count
FROM treatments
GROUP BY treatment_type, description
ORDER BY treatment_type, description;

-- Example 73
-- Question: What is the total cost of all treatment records?
SELECT SUM(cost) AS total_treatment_cost
FROM treatments;

-- Example 74
-- Question: How many treatments cost between 1,000 and 2,000, inclusive?
SELECT COUNT(*) AS treatment_count
FROM treatments
WHERE cost BETWEEN 1000 AND 2000;

-- Example 75
-- Question: How many treatments cost under 1,000, from 1,000 up to but not including 3,000, or at least 3,000?
SELECT CASE
           WHEN cost < 1000 THEN 'Under 1,000'
           WHEN cost < 3000 THEN '1,000 to under 3,000'
           ELSE '3,000 and above'
       END AS cost_band,
       COUNT(*) AS treatment_count
FROM treatments
GROUP BY cost_band
ORDER BY MIN(cost);

-- Example 76
-- Question: Which treatment types have a total treatment cost above 100,000?
SELECT treatment_type, SUM(cost) AS total_treatment_cost
FROM treatments
GROUP BY treatment_type
HAVING SUM(cost) > 100000
ORDER BY total_treatment_cost DESC, treatment_type;

-- Example 77
-- Question: Which treatment date has the highest total cost? Use the earliest date to break ties.
SELECT treatment_date, SUM(cost) AS total_treatment_cost
FROM treatments
GROUP BY treatment_date
ORDER BY total_treatment_cost DESC, treatment_date
LIMIT 1;

-- Example 78
-- Question: How many MRI treatment records are dated in the second half of 2023?
SELECT COUNT(*) AS treatment_count
FROM treatments
WHERE treatment_type = 'MRI'
  AND treatment_date >= '2023-07-01'
  AND treatment_date < '2024-01-01';

-- Example 79
-- Question: How many Chemotherapy treatments have the description Advanced protocol?
SELECT COUNT(*) AS treatment_count
FROM treatments
WHERE treatment_type = 'Chemotherapy'
  AND description = 'Advanced protocol';

-- Example 80
-- Question: Which treatment types have an average cost above 2,700?
SELECT treatment_type, ROUND(AVG(cost), 2) AS average_cost
FROM treatments
GROUP BY treatment_type
HAVING AVG(cost) > 2700
ORDER BY average_cost DESC, treatment_type;

-- Example 81
-- Question: What are the count and total cost of treatments for each treatment type?
SELECT treatment_type, COUNT(*) AS treatment_count,
       SUM(cost) AS total_treatment_cost
FROM treatments
GROUP BY treatment_type
ORDER BY total_treatment_cost DESC, treatment_type;

-- Example 82
-- Question: Which treatments have no description recorded?
SELECT treatment_id, treatment_type, treatment_date, cost
FROM treatments
WHERE description IS NULL OR TRIM(description) = ''
ORDER BY treatment_date, treatment_id;

-- Example 83
-- Question: What are the earliest and latest recorded treatment dates?
SELECT MIN(treatment_date) AS first_treatment_date,
       MAX(treatment_date) AS last_treatment_date
FROM treatments;

-- Example 84
-- Question: How many distinct appointments have at least one treatment record?
SELECT COUNT(DISTINCT appointment_id) AS appointment_count
FROM treatments;

-- Example 85
-- Question: Which appointments have more than one treatment record?
SELECT appointment_id, COUNT(*) AS treatment_count
FROM treatments
GROUP BY appointment_id
HAVING COUNT(*) > 1
ORDER BY treatment_count DESC, appointment_id;

-- Example 86
-- Question: What is the total amount of bills currently marked Paid?
SELECT SUM(amount) AS total_paid_amount
FROM billing
WHERE payment_status = 'Paid';

-- Example 87
-- Question: What is the total billed amount for each payment status?
SELECT payment_status, SUM(amount) AS total_amount
FROM billing
GROUP BY payment_status
ORDER BY payment_status;

-- Example 88
-- Question: How many bills currently marked Paid use each payment method?
SELECT payment_method, COUNT(*) AS paid_bill_count
FROM billing
WHERE payment_status = 'Paid'
GROUP BY payment_method
ORDER BY payment_method;

-- Example 89
-- Question: Which payment method has the largest total amount of Failed bills? Use the method name to break ties.
SELECT payment_method, SUM(amount) AS failed_amount
FROM billing
WHERE payment_status = 'Failed'
GROUP BY payment_method
ORDER BY failed_amount DESC, payment_method
LIMIT 1;

-- Example 90
-- Question: What is the average bill amount for each payment method, including all payment statuses?
SELECT payment_method, ROUND(AVG(amount), 2) AS average_bill_amount
FROM billing
GROUP BY payment_method
ORDER BY payment_method;

-- Example 91
-- Question: Which Failed bills have an amount greater than 4,000?
SELECT bill_id, patient_id, bill_date, amount, payment_method
FROM billing
WHERE payment_status = 'Failed'
  AND amount > 4000
ORDER BY amount DESC, bill_id;

-- Example 92
-- Question: What is the total amount billed in each month of 2023, using the bill issue date and showing months with bills only?
SELECT DATE_FORMAT(bill_date, '%Y-%m') AS bill_issue_month,
       SUM(amount) AS total_billed
FROM billing
WHERE bill_date >= '2023-01-01'
  AND bill_date < '2024-01-01'
GROUP BY DATE_FORMAT(bill_date, '%Y-%m')
ORDER BY bill_issue_month;

-- Example 93
-- Question: For bills issued in 2023 and currently marked Paid, what is the total amount by bill issue month?
-- bill_date is the issue date, not the payment date.
SELECT DATE_FORMAT(bill_date, '%Y-%m') AS bill_issue_month,
       SUM(amount) AS total_paid_amount
FROM billing
WHERE payment_status = 'Paid'
  AND bill_date >= '2023-01-01'
  AND bill_date < '2024-01-01'
GROUP BY DATE_FORMAT(bill_date, '%Y-%m')
ORDER BY bill_issue_month;

-- Example 94
-- Question: How many bills are recorded for each payment method and payment status combination?
SELECT payment_method, payment_status, COUNT(*) AS bill_count
FROM billing
GROUP BY payment_method, payment_status
ORDER BY payment_method, payment_status;

-- Example 95
-- Question: What percentage of bills are currently marked Paid for each payment method?
SELECT payment_method,
       ROUND(
           100.0 * SUM(CASE WHEN payment_status = 'Paid' THEN 1 ELSE 0 END)
           / COUNT(*), 1
       ) AS paid_percent
FROM billing
GROUP BY payment_method
ORDER BY payment_method;

-- Example 96
-- Question: Show the largest single bill, using bill ID to break ties.
SELECT bill_id, patient_id, amount, bill_date, payment_status
FROM billing
ORDER BY amount DESC, bill_id
LIMIT 1;

-- Example 97
-- Question: How many bills were issued in December 2023?
SELECT COUNT(*) AS bill_count
FROM billing
WHERE bill_date >= '2023-12-01'
  AND bill_date < '2024-01-01';

-- Example 98
-- Question: What is the total outstanding amount, defining outstanding as Pending or Failed bills?
SELECT SUM(amount) AS outstanding_amount
FROM billing
WHERE payment_status IN ('Pending', 'Failed');

-- Example 99
-- Question: How many distinct billed patients are recorded in each payment status? A patient may count in multiple statuses.
SELECT payment_status, COUNT(DISTINCT patient_id) AS patient_count
FROM billing
GROUP BY payment_status
ORDER BY payment_status;

-- Example 100
-- Question: Compare the average bill amount of Paid bills with Pending or Failed bills.
SELECT CASE
           WHEN payment_status = 'Paid' THEN 'Paid'
           ELSE 'Pending or Failed'
       END AS payment_group,
       ROUND(AVG(amount), 2) AS average_bill_amount,
       COUNT(*) AS bill_count
FROM billing
GROUP BY payment_group
ORDER BY payment_group;

-- Example 101
-- Question: Which bills were issued from 1 July through 7 July 2023, inclusive?
SELECT bill_id, patient_id, bill_date, amount, payment_status
FROM billing
WHERE bill_date >= '2023-07-01'
  AND bill_date < '2023-07-08'
ORDER BY bill_date, bill_id;

-- Example 102
-- Question: How many bills with an amount above 3,000 are recorded in each payment status?
SELECT payment_status, COUNT(*) AS bill_count
FROM billing
WHERE amount > 3000
GROUP BY payment_status
ORDER BY payment_status;

-- Example 103
-- Question: For bills whose payment method is Insurance, what is the total amount in each payment status?
SELECT payment_status, SUM(amount) AS total_amount
FROM billing
WHERE payment_method = 'Insurance'
GROUP BY payment_status
ORDER BY payment_status;

-- Example 104
-- Question: For each payment method, show the Paid, Pending and Failed amounts in separate columns.
SELECT payment_method,
       SUM(CASE WHEN payment_status = 'Paid' THEN amount ELSE 0 END) AS paid_amount,
       SUM(CASE WHEN payment_status = 'Pending' THEN amount ELSE 0 END) AS pending_amount,
       SUM(CASE WHEN payment_status = 'Failed' THEN amount ELSE 0 END) AS failed_amount
FROM billing
GROUP BY payment_method
ORDER BY payment_method;

-- Example 105
-- Question: What is the total amount of Pending bills for each payment method that has Pending bills?
SELECT payment_method, SUM(amount) AS pending_amount
FROM billing
WHERE payment_status = 'Pending'
GROUP BY payment_method
ORDER BY payment_method;

-- Example 106
-- Question: Which treatments have more than one bill?
SELECT treatment_id, COUNT(*) AS bill_count
FROM billing
GROUP BY treatment_id
HAVING COUNT(*) > 1
ORDER BY bill_count DESC, treatment_id;

-- Example 107
-- Question: What are the earliest and latest bill issue dates?
SELECT MIN(bill_date) AS first_bill_date, MAX(bill_date) AS last_bill_date
FROM billing;

-- Example 108
-- Question: How many bills have an amount of zero?
SELECT COUNT(*) AS zero_amount_bill_count
FROM billing
WHERE amount = 0;

-- Example 109
-- Question: List all bills belonging to billed patient P005, regardless of payment status.
SELECT bill_id, treatment_id, bill_date, amount, payment_method, payment_status
FROM billing
WHERE patient_id = 'P005'
ORDER BY bill_date, bill_id;

-- Example 110
-- Question: Which billed patients have both a Pending bill and a Failed bill?
SELECT patient_id
FROM billing
GROUP BY patient_id
HAVING SUM(CASE WHEN payment_status = 'Pending' THEN 1 ELSE 0 END) > 0
   AND SUM(CASE WHEN payment_status = 'Failed' THEN 1 ELSE 0 END) > 0
ORDER BY patient_id;

-- Example 111
-- Question: List every appointment with its patient's name.
SELECT a.appointment_id, a.appointment_date, a.appointment_time,
       CONCAT(p.first_name, ' ', p.last_name) AS patient_name, a.status
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
ORDER BY a.appointment_date, a.appointment_time, a.appointment_id;

-- Example 112
-- Question: How many appointments of each status does each doctor have? Show existing doctor-status combinations only.
SELECT d.doctor_id, d.first_name, d.last_name, a.status,
       COUNT(*) AS appointment_count
FROM appointments AS a
JOIN doctors AS d ON a.doctor_id = d.doctor_id
GROUP BY d.doctor_id, d.first_name, d.last_name, a.status
ORDER BY d.doctor_id, a.status;

-- Example 113
-- Question: How many appointments of any status are associated with each specialization that has appointments?
SELECT d.specialization, COUNT(*) AS appointment_count
FROM appointments AS a
JOIN doctors AS d ON a.doctor_id = d.doctor_id
GROUP BY d.specialization
ORDER BY d.specialization;

-- Example 114
-- Question: How many appointments of any status are associated with each hospital branch that has appointments?
SELECT d.hospital_branch, COUNT(*) AS appointment_count
FROM appointments AS a
JOIN doctors AS d ON a.doctor_id = d.doctor_id
GROUP BY d.hospital_branch
ORDER BY d.hospital_branch;

-- Example 115
-- Question: Which patients have more than six appointments, counting every status?
SELECT p.patient_id, p.first_name, p.last_name, COUNT(*) AS appointment_count
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
GROUP BY p.patient_id, p.first_name, p.last_name
HAVING COUNT(*) > 6
ORDER BY appointment_count DESC, p.patient_id;

-- Example 116
-- Question: Which doctors has patient P001 had a Completed appointment with?
SELECT DISTINCT d.doctor_id, d.first_name, d.last_name, d.specialization
FROM appointments AS a
JOIN doctors AS d ON a.doctor_id = d.doctor_id
WHERE a.patient_id = 'P001'
  AND a.status = 'Completed'
ORDER BY d.doctor_id;

-- Example 117
-- Question: For patients with Completed appointments, how many distinct doctors have they had Completed appointments with?
SELECT p.patient_id, p.first_name, p.last_name,
       COUNT(DISTINCT a.doctor_id) AS doctor_count
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
WHERE a.status = 'Completed'
GROUP BY p.patient_id, p.first_name, p.last_name
ORDER BY doctor_count DESC, p.patient_id;

-- Example 118
-- Question: For doctors with Completed appointments, how many distinct patients have they seen in those appointments?
SELECT d.doctor_id, d.first_name, d.last_name,
       COUNT(DISTINCT a.patient_id) AS patient_count
FROM appointments AS a
JOIN doctors AS d ON a.doctor_id = d.doctor_id
WHERE a.status = 'Completed'
GROUP BY d.doctor_id, d.first_name, d.last_name
ORDER BY patient_count DESC, d.doctor_id;

-- Example 119
-- Question: For female patients, how many appointments of any status are recorded for each reason for visit?
SELECT a.reason_for_visit, COUNT(*) AS appointment_count
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
WHERE p.gender = 'F'
GROUP BY a.reason_for_visit
ORDER BY a.reason_for_visit;

-- Example 120
-- Question: For each reason for visit, what is the average patient age at the appointment date, counting each appointment once and including every status?
SELECT a.reason_for_visit,
       ROUND(AVG(TIMESTAMPDIFF(YEAR, p.date_of_birth, a.appointment_date)), 1) AS average_age_at_appointment
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
GROUP BY a.reason_for_visit
ORDER BY a.reason_for_visit;

-- Example 121
-- Question: How many treatment records are linked to appointments in each appointment status?
SELECT a.status, COUNT(t.treatment_id) AS treatment_count
FROM treatments AS t
JOIN appointments AS a ON t.appointment_id = a.appointment_id
GROUP BY a.status
ORDER BY a.status;

-- Example 122
-- Question: What is the total treatment cost for each reason for visit that has treatment records?
SELECT a.reason_for_visit, SUM(t.cost) AS total_treatment_cost
FROM treatments AS t
JOIN appointments AS a ON t.appointment_id = a.appointment_id
GROUP BY a.reason_for_visit
ORDER BY total_treatment_cost DESC, a.reason_for_visit;

-- Example 123
-- Question: What is the total billed amount grouped by the billed patient's recorded insurance provider, including a NULL group?
SELECT p.insurance_provider, SUM(b.amount) AS total_billed
FROM billing AS b
JOIN patients AS p ON b.patient_id = p.patient_id
GROUP BY p.insurance_provider
ORDER BY p.insurance_provider;

-- Example 124
-- Question: Which 10 billed patients have the largest totals of Paid bills?
SELECT p.patient_id, p.first_name, p.last_name, SUM(b.amount) AS total_paid
FROM billing AS b
JOIN patients AS p ON b.patient_id = p.patient_id
WHERE b.payment_status = 'Paid'
GROUP BY p.patient_id, p.first_name, p.last_name
ORDER BY total_paid DESC, p.patient_id
LIMIT 10;

-- Example 125
-- Question: How many bills and what total amount belong to billed patients of each gender?
SELECT p.gender, COUNT(*) AS bill_count, SUM(b.amount) AS total_billed
FROM billing AS b
JOIN patients AS p ON b.patient_id = p.patient_id
GROUP BY p.gender
ORDER BY p.gender;

-- Example 126
-- Question: Which treatment records belong to patient P005 through their appointments?
SELECT t.treatment_id, t.treatment_type, t.cost,
       t.treatment_date, a.appointment_id, a.appointment_date
FROM treatments AS t
JOIN appointments AS a ON t.appointment_id = a.appointment_id
WHERE a.patient_id = 'P005'
ORDER BY a.appointment_date, a.appointment_id, t.treatment_id;

-- Example 127
-- Question: Which appointments have at least one MRI treatment? Show each appointment once.
SELECT DISTINCT a.appointment_id, a.appointment_date, a.patient_id, a.doctor_id
FROM appointments AS a
JOIN treatments AS t ON t.appointment_id = a.appointment_id
WHERE t.treatment_type = 'MRI'
ORDER BY a.appointment_date, a.appointment_id;

-- Example 128
-- Question: Which doctors had appointments of any status on 16 August 2023?
SELECT DISTINCT d.doctor_id, d.first_name, d.last_name
FROM appointments AS a
JOIN doctors AS d ON a.doctor_id = d.doctor_id
WHERE a.appointment_date = '2023-08-16'
ORDER BY d.doctor_id;

-- Example 129
-- Question: How many Emergency appointments of any status are associated with each specialization?
SELECT d.specialization, COUNT(*) AS appointment_count
FROM appointments AS a
JOIN doctors AS d ON a.doctor_id = d.doctor_id
WHERE a.reason_for_visit = 'Emergency'
GROUP BY d.specialization
ORDER BY d.specialization;

-- Example 130
-- Question: What is the average cost per treatment record for each reason for visit?
SELECT a.reason_for_visit, ROUND(AVG(t.cost), 2) AS average_treatment_cost
FROM treatments AS t
JOIN appointments AS a ON t.appointment_id = a.appointment_id
GROUP BY a.reason_for_visit
ORDER BY a.reason_for_visit;

-- Example 131
-- Question: What is the total billed amount for each treatment type with bills?
SELECT t.treatment_type, SUM(b.amount) AS total_billed
FROM billing AS b
JOIN treatments AS t ON b.treatment_id = t.treatment_id
GROUP BY t.treatment_type
ORDER BY t.treatment_type;

-- Example 132
-- Question: How many bills for X-Ray treatments are recorded in each payment status?
SELECT b.payment_status, COUNT(*) AS bill_count
FROM billing AS b
JOIN treatments AS t ON b.treatment_id = t.treatment_id
WHERE t.treatment_type = 'X-Ray'
GROUP BY b.payment_status
ORDER BY b.payment_status;

-- Example 133
-- Question: How many patients registered in 2023 have at least one appointment of any status?
SELECT COUNT(DISTINCT p.patient_id) AS patient_count
FROM patients AS p
JOIN appointments AS a ON a.patient_id = p.patient_id
WHERE p.registration_date >= '2023-01-01'
  AND p.registration_date < '2024-01-01';

-- Example 134
-- Question: How many appointments of any status are associated with patients of each recorded insurance provider, including a NULL group?
SELECT p.insurance_provider, COUNT(*) AS appointment_count
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
GROUP BY p.insurance_provider
ORDER BY p.insurance_provider;

-- Example 135
-- Question: How many appointments of each reason for visit are recorded at each hospital branch?
SELECT d.hospital_branch, a.reason_for_visit, COUNT(*) AS appointment_count
FROM appointments AS a
JOIN doctors AS d ON a.doctor_id = d.doctor_id
GROUP BY d.hospital_branch, a.reason_for_visit
ORDER BY d.hospital_branch, appointment_count DESC, a.reason_for_visit;

-- Example 136
-- Question: Among doctors with appointments, what is each doctor's latest recorded appointment date, regardless of status?
SELECT d.doctor_id, d.first_name, d.last_name,
       MAX(a.appointment_date) AS latest_appointment_date
FROM appointments AS a
JOIN doctors AS d ON a.doctor_id = d.doctor_id
GROUP BY d.doctor_id, d.first_name, d.last_name
ORDER BY d.doctor_id;

-- Example 137
-- Question: Among patients with appointments, what is each patient's earliest appointment date, regardless of status?
SELECT p.patient_id, p.first_name, p.last_name,
       MIN(a.appointment_date) AS first_appointment_date
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
GROUP BY p.patient_id, p.first_name, p.last_name
ORDER BY first_appointment_date, p.patient_id;

-- Example 138
-- Question: For patients with appointments, what is the signed day difference from registration to their earliest appointment date?
SELECT p.patient_id, p.registration_date,
       MIN(a.appointment_date) AS first_appointment_date,
       DATEDIFF(MIN(a.appointment_date), p.registration_date) AS days_from_registration
FROM patients AS p
JOIN appointments AS a ON a.patient_id = p.patient_id
GROUP BY p.patient_id, p.registration_date
ORDER BY p.patient_id;

-- Example 139
-- Question: Which appointments are dated before their patient's registration date?
SELECT a.appointment_id, a.patient_id, a.appointment_date, p.registration_date
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
WHERE a.appointment_date < p.registration_date
ORDER BY a.appointment_date, a.appointment_id;

-- Example 140
-- Question: How many Failed bills belong to billed patients whose insurance provider is WellnessCorp?
SELECT COUNT(*) AS failed_bill_count
FROM billing AS b
JOIN patients AS p ON b.patient_id = p.patient_id
WHERE p.insurance_provider = 'WellnessCorp'
  AND b.payment_status = 'Failed';

-- Example 141
-- Question: How many appointments does every doctor have, including doctors with no appointments?
SELECT d.doctor_id, d.first_name, d.last_name,
       COUNT(a.appointment_id) AS appointment_count
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
GROUP BY d.doctor_id, d.first_name, d.last_name
ORDER BY d.doctor_id;

-- Example 142
-- Question: Which patients have never had an appointment of any status?
SELECT p.patient_id, p.first_name, p.last_name
FROM patients AS p
LEFT JOIN appointments AS a ON a.patient_id = p.patient_id
WHERE a.appointment_id IS NULL
ORDER BY p.patient_id;

-- Example 143
-- Question: Which doctors had no appointments in December 2023?
SELECT d.doctor_id, d.first_name, d.last_name
FROM doctors AS d
LEFT JOIN appointments AS a
    ON a.doctor_id = d.doctor_id
   AND a.appointment_date >= '2023-12-01'
   AND a.appointment_date < '2024-01-01'
WHERE a.appointment_id IS NULL
ORDER BY d.doctor_id;

-- Example 144
-- Question: How many appointments did every patient have in 2023, including patients with none?
SELECT p.patient_id, p.first_name, p.last_name,
       COUNT(a.appointment_id) AS appointment_count
FROM patients AS p
LEFT JOIN appointments AS a
    ON a.patient_id = p.patient_id
   AND a.appointment_date >= '2023-01-01'
   AND a.appointment_date < '2024-01-01'
GROUP BY p.patient_id, p.first_name, p.last_name
ORDER BY p.patient_id;

-- Example 145
-- Question: Which treatments have no bill?
SELECT t.treatment_id, t.treatment_type, t.cost, t.treatment_date
FROM treatments AS t
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
WHERE b.bill_id IS NULL
ORDER BY t.treatment_id;

-- Example 146
-- Question: Which appointments have no treatment record?
SELECT a.appointment_id, a.appointment_date, a.status
FROM appointments AS a
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
WHERE t.treatment_id IS NULL
ORDER BY a.appointment_date, a.appointment_id;

-- Example 147
-- Question: How many Completed appointments does every doctor have, including doctors with zero?
SELECT d.doctor_id, d.first_name, d.last_name,
       COUNT(a.appointment_id) AS completed_appointment_count
FROM doctors AS d
LEFT JOIN appointments AS a
    ON a.doctor_id = d.doctor_id
   AND a.status = 'Completed'
GROUP BY d.doctor_id, d.first_name, d.last_name
ORDER BY d.doctor_id;

-- Example 148
-- Question: How many Completed appointments does each recorded specialization have, including specializations with zero?
SELECT d.specialization, COUNT(a.appointment_id) AS completed_appointment_count
FROM doctors AS d
LEFT JOIN appointments AS a
    ON a.doctor_id = d.doctor_id
   AND a.status = 'Completed'
GROUP BY d.specialization
ORDER BY d.specialization;

-- Example 149
-- Question: What is the total billed amount associated with each doctor, showing zero for doctors with no bills?
-- Zero is explicitly requested for an aggregate total; it does not mean an individual bill exists.
SELECT d.doctor_id, d.first_name, d.last_name,
       COALESCE(SUM(b.amount), 0) AS total_billed
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
GROUP BY d.doctor_id, d.first_name, d.last_name
ORDER BY d.doctor_id;

-- Example 150
-- Question: What is every patient's total amount of Paid bills, showing zero for patients with no Paid bills?
SELECT p.patient_id, p.first_name, p.last_name,
       COALESCE(SUM(b.amount), 0) AS total_paid
FROM patients AS p
LEFT JOIN billing AS b
    ON b.patient_id = p.patient_id
   AND b.payment_status = 'Paid'
GROUP BY p.patient_id, p.first_name, p.last_name
ORDER BY p.patient_id;

-- Example 151
-- Question: How many treatment records are associated with every doctor, including doctors with none?
SELECT d.doctor_id, d.first_name, d.last_name,
       COUNT(t.treatment_id) AS treatment_count
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
GROUP BY d.doctor_id, d.first_name, d.last_name
ORDER BY d.doctor_id;

-- Example 152
-- Question: Which patients have never had a Completed appointment?
SELECT p.patient_id, p.first_name, p.last_name
FROM patients AS p
LEFT JOIN appointments AS a
    ON a.patient_id = p.patient_id
   AND a.status = 'Completed'
WHERE a.appointment_id IS NULL
ORDER BY p.patient_id;

-- Example 153
-- Question: For every patient, how many distinct doctors have they had Completed appointments with, including zero?
SELECT p.patient_id, p.first_name, p.last_name,
       COUNT(DISTINCT a.doctor_id) AS doctor_count
FROM patients AS p
LEFT JOIN appointments AS a
    ON a.patient_id = p.patient_id
   AND a.status = 'Completed'
GROUP BY p.patient_id, p.first_name, p.last_name
ORDER BY p.patient_id;

-- Example 154
-- Question: How many bills belong to each patient, including patients with no bills?
SELECT p.patient_id, p.first_name, p.last_name,
       COUNT(b.bill_id) AS bill_count
FROM patients AS p
LEFT JOIN billing AS b ON b.patient_id = p.patient_id
GROUP BY p.patient_id, p.first_name, p.last_name
ORDER BY p.patient_id;

-- Example 155
-- Question: When was each doctor's latest Completed appointment? Include doctors without one and leave their date missing.
SELECT d.doctor_id, d.first_name, d.last_name,
       MAX(a.appointment_date) AS latest_completed_date
FROM doctors AS d
LEFT JOIN appointments AS a
    ON a.doctor_id = d.doctor_id
   AND a.status = 'Completed'
GROUP BY d.doctor_id, d.first_name, d.last_name
ORDER BY d.doctor_id;

-- Example 156
-- Question: How many appointments and distinct treatment types are associated with every doctor, including doctors with no appointments?
-- An appointment can have several treatments, so count distinct appointment IDs.
SELECT d.doctor_id, d.first_name, d.last_name,
       COUNT(DISTINCT a.appointment_id) AS appointment_count,
       COUNT(DISTINCT t.treatment_type) AS treatment_type_count
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
GROUP BY d.doctor_id, d.first_name, d.last_name
ORDER BY d.doctor_id;

-- Example 157
-- Question: Show every appointment with its treatment count, including appointments with no treatments.
SELECT a.appointment_id, a.appointment_date,
       COUNT(t.treatment_id) AS treatment_count
FROM appointments AS a
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
GROUP BY a.appointment_id, a.appointment_date
ORDER BY a.appointment_date, a.appointment_id;

-- Example 158
-- Question: Show every appointment with its bill count, including appointments with no bills.
SELECT a.appointment_id, a.appointment_date,
       COUNT(b.bill_id) AS bill_count
FROM appointments AS a
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
GROUP BY a.appointment_id, a.appointment_date
ORDER BY a.appointment_date, a.appointment_id;

-- Example 159
-- Question: Which appointments have no bills across any of their treatments, including appointments with no treatments?
-- HAVING checks the whole appointment; filtering individual unmatched treatments would be incorrect.
SELECT a.appointment_id, a.appointment_date
FROM appointments AS a
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
GROUP BY a.appointment_id, a.appointment_date
HAVING COUNT(b.bill_id) = 0
ORDER BY a.appointment_date, a.appointment_id;

-- Example 160
-- Question: Which appointments have at least one treatment but no bills across any treatment?
SELECT a.appointment_id, a.appointment_date
FROM appointments AS a
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
GROUP BY a.appointment_id, a.appointment_date
HAVING COUNT(DISTINCT t.treatment_id) > 0
   AND COUNT(b.bill_id) = 0
ORDER BY a.appointment_date, a.appointment_id;

-- Example 161
-- Question: How many appointments were recorded in March 2023?
SELECT COUNT(*) AS appointment_count
FROM appointments
WHERE appointment_date >= '2023-03-01'
  AND appointment_date < '2023-04-01';

-- Example 162
-- Question: How many appointments were recorded in the calendar month immediately before January 2023?
SELECT COUNT(*) AS appointment_count
FROM appointments
WHERE appointment_date >= '2022-12-01'
  AND appointment_date < '2023-01-01';

-- Example 163
-- Question: How many appointments were recorded in February 2024, including leap day?
SELECT COUNT(*) AS appointment_count
FROM appointments
WHERE appointment_date >= '2024-02-01'
  AND appointment_date < '2024-03-01';

-- Example 164
-- Question: How many appointments were recorded in December 2023?
SELECT COUNT(*) AS appointment_count
FROM appointments
WHERE appointment_date >= '2023-12-01'
  AND appointment_date < '2024-01-01';

-- Example 165
-- Question: How many appointments are recorded in each calendar year represented in the data?
SELECT YEAR(appointment_date) AS appointment_year, COUNT(*) AS appointment_count
FROM appointments
GROUP BY YEAR(appointment_date)
ORDER BY appointment_year;

-- Example 166
-- Question: How many appointments are recorded in the 30-day period ending on 31 December 2023, including that day?
SELECT COUNT(*) AS appointment_count
FROM appointments
WHERE appointment_date >= '2023-12-02'
  AND appointment_date < '2024-01-01';

-- Example 167
-- Question: What is the total amount of bills issued in the fourth quarter of 2023?
SELECT SUM(amount) AS total_billed
FROM billing
WHERE bill_date >= '2023-10-01'
  AND bill_date < '2024-01-01';

-- Example 168
-- Question: Which bill issue month of 2023 has the highest total billed amount? Use the earliest month to break ties.
SELECT DATE_FORMAT(bill_date, '%Y-%m') AS bill_issue_month,
       SUM(amount) AS total_billed
FROM billing
WHERE bill_date >= '2023-01-01'
  AND bill_date < '2024-01-01'
GROUP BY DATE_FORMAT(bill_date, '%Y-%m')
ORDER BY total_billed DESC, bill_issue_month
LIMIT 1;

-- Example 169
-- Question: Group appointments dated within calendar year 2023 by their ISO week labels, showing weeks with appointments only.
-- An ISO week label may belong to the previous ISO year at the start of January.
SELECT DATE_FORMAT(appointment_date, '%x-W%v') AS iso_week,
       COUNT(*) AS appointment_count
FROM appointments
WHERE appointment_date >= '2023-01-01'
  AND appointment_date < '2024-01-01'
GROUP BY DATE_FORMAT(appointment_date, '%x-W%v')
ORDER BY iso_week;

-- Example 170
-- Question: Which treatments are dated from 1 December through 15 December 2023, inclusive?
SELECT treatment_id, treatment_type, cost, treatment_date
FROM treatments
WHERE treatment_date >= '2023-12-01'
  AND treatment_date < '2023-12-16'
ORDER BY treatment_date, treatment_id;

-- Example 171
-- Question: Show appointment counts for January 2023 and February 2023 in separate columns, including zero counts.
SELECT COUNT(CASE
                 WHEN appointment_date >= '2023-01-01'
                  AND appointment_date < '2023-02-01' THEN 1
             END) AS january_count,
       COUNT(CASE
                 WHEN appointment_date >= '2023-02-01'
                  AND appointment_date < '2023-03-01' THEN 1
             END) AS february_count
FROM appointments;

-- Example 172
-- Question: How many appointments are recorded in each month from November 2022 through February 2023? Omit months with no appointments.
SELECT DATE_FORMAT(appointment_date, '%Y-%m') AS appointment_month,
       COUNT(*) AS appointment_count
FROM appointments
WHERE appointment_date >= '2022-11-01'
  AND appointment_date < '2023-03-01'
GROUP BY DATE_FORMAT(appointment_date, '%Y-%m')
ORDER BY appointment_month;

-- Example 173
-- Question: What amount was billed using the Insurance payment method in each bill issue month of 2023 that has matching bills?
SELECT DATE_FORMAT(bill_date, '%Y-%m') AS bill_issue_month,
       SUM(amount) AS insurance_billed
FROM billing
WHERE payment_method = 'Insurance'
  AND bill_date >= '2023-01-01'
  AND bill_date < '2024-01-01'
GROUP BY DATE_FORMAT(bill_date, '%Y-%m')
ORDER BY bill_issue_month;

-- Example 174
-- Question: Among patients with appointments, how many days separate each patient's earliest and latest appointment dates?
SELECT patient_id, MIN(appointment_date) AS first_appointment,
       MAX(appointment_date) AS last_appointment,
       DATEDIFF(MAX(appointment_date), MIN(appointment_date)) AS days_between
FROM appointments
GROUP BY patient_id
ORDER BY days_between DESC, patient_id;

-- Example 175
-- Question: How many appointments are recorded on each date in March 2023? Show dates with appointments only.
SELECT appointment_date, COUNT(*) AS appointment_count
FROM appointments
WHERE appointment_date >= '2023-03-01'
  AND appointment_date < '2023-04-01'
GROUP BY appointment_date
ORDER BY appointment_date;

-- Example 176
-- Question: How many patients registered in the last three calendar months of 2023?
SELECT COUNT(*) AS patient_count
FROM patients
WHERE registration_date >= '2023-10-01'
  AND registration_date < '2024-01-01';

-- Example 177
-- Question: How many appointments are dated after 31 December 2023, regardless of status?
SELECT COUNT(*) AS appointment_count
FROM appointments
WHERE appointment_date >= '2024-01-01';

-- Example 178
-- Question: Which bills were issued before their linked treatment date?
SELECT b.bill_id, b.bill_date, t.treatment_id, t.treatment_date, b.amount
FROM billing AS b
JOIN treatments AS t ON b.treatment_id = t.treatment_id
WHERE b.bill_date < t.treatment_date
ORDER BY b.bill_date, b.bill_id;

-- Example 179
-- Question: For each payment status, what is the average signed day difference from treatment date to bill issue date?
SELECT b.payment_status,
       ROUND(AVG(DATEDIFF(b.bill_date, t.treatment_date)), 1) AS average_signed_days
FROM billing AS b
JOIN treatments AS t ON b.treatment_id = t.treatment_id
GROUP BY b.payment_status
ORDER BY b.payment_status;

-- Example 180
-- Question: Which treatments are dated before their linked appointment date?
SELECT t.treatment_id, t.treatment_date, a.appointment_id, a.appointment_date
FROM treatments AS t
JOIN appointments AS a ON t.appointment_id = a.appointment_id
WHERE t.treatment_date < a.appointment_date
ORDER BY t.treatment_date, t.treatment_id;

-- Example 181
-- Question: For each specialization with appointments, what percentage of its appointments are Cancelled?
SELECT d.specialization,
       ROUND(
           100.0 * SUM(CASE WHEN a.status = 'Cancelled' THEN 1 ELSE 0 END)
           / COUNT(*), 1
       ) AS cancelled_percent
FROM appointments AS a
JOIN doctors AS d ON a.doctor_id = d.doctor_id
GROUP BY d.specialization
ORDER BY d.specialization;

-- Example 182
-- Question: Which doctors have more than 20 appointments in total and fewer than six Completed appointments?
SELECT d.doctor_id, d.first_name, d.last_name,
       COUNT(*) AS appointment_count,
       SUM(CASE WHEN a.status = 'Completed' THEN 1 ELSE 0 END) AS completed_count
FROM appointments AS a
JOIN doctors AS d ON a.doctor_id = d.doctor_id
GROUP BY d.doctor_id, d.first_name, d.last_name
HAVING COUNT(*) > 20
   AND SUM(CASE WHEN a.status = 'Completed' THEN 1 ELSE 0 END) < 6
ORDER BY d.doctor_id;

-- Example 183
-- Question: Which hospital branch has the highest average doctor experience? Use the branch name to break ties.
SELECT hospital_branch, ROUND(AVG(years_experience), 1) AS average_years_experience
FROM doctors
GROUP BY hospital_branch
ORDER BY AVG(years_experience) DESC, hospital_branch
LIMIT 1;

-- Example 184
-- Question: How many appointments involved patients aged at least 60 at the appointment date, grouped by specialization?
SELECT d.specialization, COUNT(*) AS appointment_count
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
JOIN doctors AS d ON a.doctor_id = d.doctor_id
WHERE TIMESTAMPDIFF(YEAR, p.date_of_birth, a.appointment_date) >= 60
GROUP BY d.specialization
ORDER BY d.specialization;

-- Example 185
-- Question: Which doctor email addresses end with @hospital.com?
SELECT doctor_id, first_name, last_name, email
FROM doctors
WHERE email LIKE '%@hospital.com'
ORDER BY doctor_id;

-- Example 186
-- Question: For every doctor at Westside Clinic, show the number of distinct patients with appointments and the total appointment count, including zero.
SELECT d.doctor_id, d.first_name, d.last_name,
       COUNT(DISTINCT a.patient_id) AS patient_count,
       COUNT(a.appointment_id) AS appointment_count
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
WHERE d.hospital_branch = 'Westside Clinic'
GROUP BY d.doctor_id, d.first_name, d.last_name
ORDER BY d.doctor_id;

-- Example 187
-- Question: Which hospital branches have more than two doctors?
SELECT hospital_branch, COUNT(*) AS doctor_count
FROM doctors
GROUP BY hospital_branch
HAVING COUNT(*) > 2
ORDER BY doctor_count DESC, hospital_branch;

-- Example 188
-- Question: Which patients have appointments with at least three distinct doctors, counting every appointment status?
SELECT p.patient_id, p.first_name, p.last_name,
       COUNT(DISTINCT a.doctor_id) AS doctor_count
FROM patients AS p
JOIN appointments AS a ON a.patient_id = p.patient_id
GROUP BY p.patient_id, p.first_name, p.last_name
HAVING COUNT(DISTINCT a.doctor_id) >= 3
ORDER BY doctor_count DESC, p.patient_id;

-- Example 189
-- Question: Which patients have both a Completed appointment and a Cancelled appointment?
SELECT p.patient_id, p.first_name, p.last_name
FROM patients AS p
JOIN appointments AS a ON a.patient_id = p.patient_id
GROUP BY p.patient_id, p.first_name, p.last_name
HAVING SUM(CASE WHEN a.status = 'Completed' THEN 1 ELSE 0 END) > 0
   AND SUM(CASE WHEN a.status = 'Cancelled' THEN 1 ELSE 0 END) > 0
ORDER BY p.patient_id;

-- Example 190
-- Question: Which doctors have at least five appointments and no Completed appointments?
SELECT d.doctor_id, d.first_name, d.last_name, COUNT(*) AS appointment_count
FROM doctors AS d
JOIN appointments AS a ON a.doctor_id = d.doctor_id
GROUP BY d.doctor_id, d.first_name, d.last_name
HAVING COUNT(*) >= 5
   AND SUM(CASE WHEN a.status = 'Completed' THEN 1 ELSE 0 END) = 0
ORDER BY d.doctor_id;

-- Example 191
-- Question: For every doctor, show total appointments and separate counts for Completed, Cancelled, Scheduled and No-show.
SELECT d.doctor_id, d.first_name, d.last_name,
       COUNT(a.appointment_id) AS appointment_count,
       SUM(CASE WHEN a.status = 'Completed' THEN 1 ELSE 0 END) AS completed_count,
       SUM(CASE WHEN a.status = 'Cancelled' THEN 1 ELSE 0 END) AS cancelled_count,
       SUM(CASE WHEN a.status = 'Scheduled' THEN 1 ELSE 0 END) AS scheduled_count,
       SUM(CASE WHEN a.status = 'No-show' THEN 1 ELSE 0 END) AS no_show_count
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
GROUP BY d.doctor_id, d.first_name, d.last_name
ORDER BY d.doctor_id;

-- Example 192
-- Question: For every doctor, what percentage of appointments are Completed? Leave the percentage missing when the doctor has no appointments.
SELECT d.doctor_id, d.first_name, d.last_name,
       ROUND(
           100.0 * SUM(CASE WHEN a.status = 'Completed' THEN 1 ELSE 0 END)
           / NULLIF(COUNT(a.appointment_id), 0), 1
       ) AS completed_percent
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
GROUP BY d.doctor_id, d.first_name, d.last_name
ORDER BY d.doctor_id;

-- Example 193
-- Question: What is the average cost per treatment record at each hospital branch with treatments?
SELECT d.hospital_branch, ROUND(AVG(t.cost), 2) AS average_treatment_cost
FROM treatments AS t
JOIN appointments AS a ON t.appointment_id = a.appointment_id
JOIN doctors AS d ON a.doctor_id = d.doctor_id
GROUP BY d.hospital_branch
ORDER BY d.hospital_branch;

-- Example 194
-- Question: Which patients have a Chemotherapy treatment record through their appointments?
SELECT DISTINCT p.patient_id, p.first_name, p.last_name
FROM treatments AS t
JOIN appointments AS a ON t.appointment_id = a.appointment_id
JOIN patients AS p ON a.patient_id = p.patient_id
WHERE t.treatment_type = 'Chemotherapy'
ORDER BY p.last_name, p.first_name, p.patient_id;

-- Example 195
-- Question: How many treatment records of each type are associated with each doctor? Show existing combinations only.
SELECT d.doctor_id, d.first_name, d.last_name,
       t.treatment_type, COUNT(*) AS treatment_count
FROM treatments AS t
JOIN appointments AS a ON t.appointment_id = a.appointment_id
JOIN doctors AS d ON a.doctor_id = d.doctor_id
GROUP BY d.doctor_id, d.first_name, d.last_name, t.treatment_type
ORDER BY d.doctor_id, t.treatment_type;

-- Example 196
-- Question: What is the total amount of Paid bills grouped by the linked appointment's status?
SELECT a.status AS appointment_status, SUM(b.amount) AS total_paid
FROM billing AS b
JOIN treatments AS t ON b.treatment_id = t.treatment_id
JOIN appointments AS a ON t.appointment_id = a.appointment_id
WHERE b.payment_status = 'Paid'
GROUP BY a.status
ORDER BY a.status;

-- Example 197
-- Question: Which patients have a Completed appointment with a doctor specializing in Pediatrics?
SELECT DISTINCT p.patient_id, p.first_name, p.last_name
FROM appointments AS a
JOIN doctors AS d ON a.doctor_id = d.doctor_id
JOIN patients AS p ON a.patient_id = p.patient_id
WHERE d.specialization = 'Pediatrics'
  AND a.status = 'Completed'
ORDER BY p.patient_id;

-- Example 198
-- Question: How many distinct patients have Completed appointments at each branch? A patient may count in more than one branch.
SELECT d.hospital_branch, COUNT(DISTINCT a.patient_id) AS patient_count
FROM appointments AS a
JOIN doctors AS d ON a.doctor_id = d.doctor_id
WHERE a.status = 'Completed'
GROUP BY d.hospital_branch
ORDER BY d.hospital_branch;

-- Example 199
-- Question: Show appointments dated 25 October 2023 with patient and doctor names, regardless of status.
SELECT a.appointment_id, a.appointment_time,
       CONCAT(p.first_name, ' ', p.last_name) AS patient_name,
       CONCAT(d.first_name, ' ', d.last_name) AS doctor_name,
       a.status
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
JOIN doctors AS d ON a.doctor_id = d.doctor_id
WHERE a.appointment_date = '2023-10-25'
ORDER BY a.appointment_time, a.appointment_id;

-- Example 200
-- Question: What is the total billed amount for each hospital branch and specialization with bills?
SELECT d.hospital_branch, d.specialization, SUM(b.amount) AS total_billed
FROM billing AS b
JOIN treatments AS t ON b.treatment_id = t.treatment_id
JOIN appointments AS a ON t.appointment_id = a.appointment_id
JOIN doctors AS d ON a.doctor_id = d.doctor_id
GROUP BY d.hospital_branch, d.specialization
ORDER BY d.hospital_branch, d.specialization;

-- Example 201
-- Question: List all appointments with patient name, hospital branch and any individual invoice amounts. Keep appointments without treatments or invoices.
-- One row per treatment-bill match; missing optional records remain NULL.
SELECT a.appointment_id, a.appointment_date,
       CONCAT(p.first_name, ' ', p.last_name) AS patient_name,
       d.hospital_branch, t.treatment_id, b.bill_id, b.amount AS invoice_amount
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
JOIN doctors AS d ON a.doctor_id = d.doctor_id
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
ORDER BY a.appointment_date, a.appointment_id, t.treatment_id, b.bill_id;

-- Example 202
-- Question: List only appointments that have invoices, showing patient name, branch and one row per invoice.
SELECT a.appointment_id, a.appointment_date,
       CONCAT(p.first_name, ' ', p.last_name) AS patient_name,
       d.hospital_branch, b.bill_id, b.amount AS invoice_amount
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
JOIN doctors AS d ON a.doctor_id = d.doctor_id
JOIN treatments AS t ON t.appointment_id = a.appointment_id
JOIN billing AS b ON b.treatment_id = t.treatment_id
ORDER BY a.appointment_date, a.appointment_id, b.bill_id;

-- Example 203
-- Question: List all appointments for doctor D004 in June 2023 with patient name, branch and any individual invoices, keeping appointments without invoices.
SELECT a.appointment_id, a.appointment_date,
       CONCAT(p.first_name, ' ', p.last_name) AS patient_name,
       d.hospital_branch, t.treatment_id, b.bill_id, b.amount AS invoice_amount
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
JOIN doctors AS d ON a.doctor_id = d.doctor_id
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
WHERE a.doctor_id = 'D004'
  AND a.appointment_date >= '2023-06-01'
  AND a.appointment_date < '2023-07-01'
ORDER BY a.appointment_date, a.appointment_id, t.treatment_id, b.bill_id;

-- Example 204
-- Question: List all Completed appointments in August 2023 with patient names and any individual invoices, retaining appointments without invoices.
SELECT a.appointment_id, a.appointment_date,
       CONCAT(p.first_name, ' ', p.last_name) AS patient_name,
       t.treatment_id, b.bill_id, b.amount AS invoice_amount
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
WHERE a.status = 'Completed'
  AND a.appointment_date >= '2023-08-01'
  AND a.appointment_date < '2023-09-01'
ORDER BY a.appointment_date, a.appointment_id, t.treatment_id, b.bill_id;

-- Example 205
-- Question: Show one row per appointment with patient name, branch, bill count and total billed amount. Include every appointment and leave the amount NULL when no bills exist.
SELECT a.appointment_id, a.appointment_date,
       p.patient_id, p.first_name, p.last_name, d.hospital_branch,
       COUNT(b.bill_id) AS bill_count, SUM(b.amount) AS total_billed
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
JOIN doctors AS d ON a.doctor_id = d.doctor_id
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
GROUP BY a.appointment_id, a.appointment_date,
         p.patient_id, p.first_name, p.last_name, d.hospital_branch
ORDER BY a.appointment_date, a.appointment_id;

-- Example 206
-- Question: Show every appointment with its total billed amount, explicitly displaying zero when it has no bills.
SELECT a.appointment_id, a.appointment_date,
       COUNT(b.bill_id) AS bill_count,
       COALESCE(SUM(b.amount), 0) AS total_billed
FROM appointments AS a
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
GROUP BY a.appointment_id, a.appointment_date
ORDER BY a.appointment_date, a.appointment_id;

-- Example 207
-- Question: Show every appointment with any Paid invoices, keeping appointments without a Paid invoice and leaving missing invoice amounts NULL.
-- The Paid filter belongs in ON so it does not remove appointments.
SELECT a.appointment_id, a.appointment_date,
       t.treatment_id, b.bill_id, b.amount AS paid_invoice_amount
FROM appointments AS a
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b
    ON b.treatment_id = t.treatment_id
   AND b.payment_status = 'Paid'
ORDER BY a.appointment_date, a.appointment_id, t.treatment_id, b.bill_id;

-- Example 208
-- Question: Show only appointments with at least one Paid invoice, with one row per Paid invoice.
SELECT a.appointment_id, a.appointment_date, b.bill_id, b.amount AS paid_invoice_amount
FROM appointments AS a
JOIN treatments AS t ON t.appointment_id = a.appointment_id
JOIN billing AS b ON b.treatment_id = t.treatment_id
WHERE b.payment_status = 'Paid'
ORDER BY a.appointment_date, a.appointment_id, b.bill_id;

-- Example 209
-- Question: For every appointment, count its Paid bills and sum their amounts. Leave the sum NULL when no Paid bills exist.
SELECT a.appointment_id, COUNT(b.bill_id) AS paid_bill_count,
       SUM(b.amount) AS total_paid
FROM appointments AS a
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b
    ON b.treatment_id = t.treatment_id
   AND b.payment_status = 'Paid'
GROUP BY a.appointment_id
ORDER BY a.appointment_id;

-- Example 210
-- Question: For every appointment, count Paid bills issued in September 2023 and sum their amounts, retaining appointments with no matching bills.
-- The period refers to bill issue dates, not appointment or payment dates.
SELECT a.appointment_id, COUNT(b.bill_id) AS paid_bill_count,
       SUM(b.amount) AS total_paid
FROM appointments AS a
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b
    ON b.treatment_id = t.treatment_id
   AND b.payment_status = 'Paid'
   AND b.bill_date >= '2023-09-01'
   AND b.bill_date < '2023-10-01'
GROUP BY a.appointment_id
ORDER BY a.appointment_id;

-- Example 211
-- Question: Show every treatment with any individual bill amounts, keeping treatments that have no bills.
SELECT t.treatment_id, t.treatment_type, t.cost AS treatment_cost,
       b.bill_id, b.amount AS invoice_amount
FROM treatments AS t
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
ORDER BY t.treatment_id, b.bill_id;

-- Example 212
-- Question: For every treatment, show its cost, bill count and total billed amount, leaving the billed total NULL when no bills exist.
SELECT t.treatment_id, t.treatment_type, t.cost AS treatment_cost,
       COUNT(b.bill_id) AS bill_count, SUM(b.amount) AS total_billed
FROM treatments AS t
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
GROUP BY t.treatment_id, t.treatment_type, t.cost
ORDER BY t.treatment_id;

-- Example 213
-- Question: Which treatments have a total billed amount greater than their recorded treatment cost?
SELECT t.treatment_id, t.cost AS treatment_cost,
       SUM(b.amount) AS total_billed
FROM treatments AS t
JOIN billing AS b ON b.treatment_id = t.treatment_id
GROUP BY t.treatment_id, t.cost
HAVING SUM(b.amount) > t.cost
ORDER BY t.treatment_id;

-- Example 214
-- Question: For every appointment, show the number of distinct treatments and the number of bills without counting a treatment again for each bill.
SELECT a.appointment_id,
       COUNT(DISTINCT t.treatment_id) AS treatment_count,
       COUNT(b.bill_id) AS bill_count
FROM appointments AS a
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
GROUP BY a.appointment_id
ORDER BY a.appointment_id;

-- Example 215
-- Question: For every doctor, count distinct appointments, distinct treatments and bills, including doctors with none.
SELECT d.doctor_id, d.first_name, d.last_name,
       COUNT(DISTINCT a.appointment_id) AS appointment_count,
       COUNT(DISTINCT t.treatment_id) AS treatment_count,
       COUNT(b.bill_id) AS bill_count
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
GROUP BY d.doctor_id, d.first_name, d.last_name
ORDER BY d.doctor_id;

-- Example 216
-- Question: For every doctor, show total treatment cost without involving bills; leave the total NULL when no treatments exist.
-- Summing treatment costs after joining bills could duplicate a treatment's cost.
SELECT d.doctor_id, d.first_name, d.last_name,
       SUM(t.cost) AS total_treatment_cost
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
GROUP BY d.doctor_id, d.first_name, d.last_name
ORDER BY d.doctor_id;

-- Example 217
-- Question: For every hospital branch recorded in doctors, show its total billed amount, leaving it NULL when no bills are linked.
SELECT d.hospital_branch, SUM(b.amount) AS total_billed
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
GROUP BY d.hospital_branch
ORDER BY d.hospital_branch;

-- Example 218
-- Question: For each branch with Paid bills, show the total Paid amount grouped by branch only, not by doctor.
SELECT d.hospital_branch, SUM(b.amount) AS total_paid
FROM billing AS b
JOIN treatments AS t ON b.treatment_id = t.treatment_id
JOIN appointments AS a ON t.appointment_id = a.appointment_id
JOIN doctors AS d ON a.doctor_id = d.doctor_id
WHERE b.payment_status = 'Paid'
GROUP BY d.hospital_branch
ORDER BY d.hospital_branch;

-- Example 219
-- Question: For each doctor with Paid bills, show the total Paid amount and the doctor's branch.
SELECT d.doctor_id, d.first_name, d.last_name,
       d.hospital_branch, SUM(b.amount) AS total_paid
FROM billing AS b
JOIN treatments AS t ON b.treatment_id = t.treatment_id
JOIN appointments AS a ON t.appointment_id = a.appointment_id
JOIN doctors AS d ON a.doctor_id = d.doctor_id
WHERE b.payment_status = 'Paid'
GROUP BY d.doctor_id, d.first_name, d.last_name, d.hospital_branch
ORDER BY d.doctor_id;

-- Example 220
-- Question: For every doctor, total bills currently marked Paid that were issued in April 2023, showing zero when there are none.
SELECT d.doctor_id, d.first_name, d.last_name,
       COALESCE(SUM(b.amount), 0) AS total_paid
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b
    ON b.treatment_id = t.treatment_id
   AND b.payment_status = 'Paid'
   AND b.bill_date >= '2023-04-01'
   AND b.bill_date < '2023-05-01'
GROUP BY d.doctor_id, d.first_name, d.last_name
ORDER BY d.doctor_id;

-- Example 221
-- Question: List each bill with the patient named on that bill.
-- Use billing.patient_id when the question asks for the billed patient.
SELECT b.bill_id, b.bill_date, b.amount,
       p.patient_id, p.first_name, p.last_name
FROM billing AS b
JOIN patients AS p ON b.patient_id = p.patient_id
ORDER BY b.bill_date, b.bill_id;

-- Example 222
-- Question: List each bill with the patient of the appointment linked through its treatment.
-- This is the appointment patient, who is not assumed to match billing.patient_id.
SELECT b.bill_id, b.amount, a.appointment_id,
       p.patient_id AS appointment_patient_id, p.first_name, p.last_name
FROM billing AS b
JOIN treatments AS t ON b.treatment_id = t.treatment_id
JOIN appointments AS a ON t.appointment_id = a.appointment_id
JOIN patients AS p ON a.patient_id = p.patient_id
ORDER BY b.bill_id;

-- Example 223
-- Question: Which bills name a different patient from the patient of the linked appointment?
SELECT b.bill_id, b.patient_id AS billed_patient_id,
       a.patient_id AS appointment_patient_id, a.appointment_id
FROM billing AS b
JOIN treatments AS t ON b.treatment_id = t.treatment_id
JOIN appointments AS a ON t.appointment_id = a.appointment_id
WHERE b.patient_id <> a.patient_id
ORDER BY b.bill_id;

-- Example 224
-- Question: How many bills name a different patient from the linked appointment's patient?
SELECT COUNT(*) AS mismatched_bill_count
FROM billing AS b
JOIN treatments AS t ON b.treatment_id = t.treatment_id
JOIN appointments AS a ON t.appointment_id = a.appointment_id
WHERE b.patient_id <> a.patient_id;

-- Example 225
-- Question: For each branch, how many distinct billed patients have bills above 30? Count a patient once per branch, even if they appear at other branches.
SELECT d.hospital_branch, COUNT(DISTINCT b.patient_id) AS billed_patient_count
FROM billing AS b
JOIN treatments AS t ON b.treatment_id = t.treatment_id
JOIN appointments AS a ON t.appointment_id = a.appointment_id
JOIN doctors AS d ON a.doctor_id = d.doctor_id
WHERE b.amount > 30
GROUP BY d.hospital_branch
ORDER BY d.hospital_branch;

-- Example 226
-- Question: For every branch recorded in doctors, show its appointment count, including branches with zero appointments.
SELECT d.hospital_branch, COUNT(a.appointment_id) AS appointment_count
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
GROUP BY d.hospital_branch
ORDER BY d.hospital_branch;

-- Example 227
-- Question: For every branch, show the count of Completed appointments and the count of all appointments, including zero.
SELECT d.hospital_branch,
       COUNT(a.appointment_id) AS appointment_count,
       SUM(CASE WHEN a.status = 'Completed' THEN 1 ELSE 0 END) AS completed_count
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
GROUP BY d.hospital_branch
ORDER BY d.hospital_branch;

-- Example 228
-- Question: For every branch, what percentage of appointments are Completed? Leave the percentage NULL if there are no appointments.
SELECT d.hospital_branch,
       ROUND(
           100.0 * SUM(CASE WHEN a.status = 'Completed' THEN 1 ELSE 0 END)
           / NULLIF(COUNT(a.appointment_id), 0), 1
       ) AS completed_percent
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
GROUP BY d.hospital_branch
ORDER BY d.hospital_branch;

-- Example 229
-- Question: Show the daily total of bills issued in June 2023, using actual dates and omitting days with no bills.
SELECT bill_date, SUM(amount) AS total_billed
FROM billing
WHERE bill_date >= '2023-06-01'
  AND bill_date < '2023-07-01'
GROUP BY bill_date
ORDER BY bill_date;

-- Example 230
-- Question: Show appointment counts on 1, 2 and 3 June 2023 in separate columns, including zero.
SELECT COUNT(CASE WHEN appointment_date = '2023-06-01' THEN 1 END) AS june_1_count,
       COUNT(CASE WHEN appointment_date = '2023-06-02' THEN 1 END) AS june_2_count,
       COUNT(CASE WHEN appointment_date = '2023-06-03' THEN 1 END) AS june_3_count
FROM appointments;

-- Example 231
-- Question: What is the total Paid bill amount for each payment method, showing all methods present in billing even when their Paid amount is zero?
SELECT payment_method,
       SUM(CASE WHEN payment_status = 'Paid' THEN amount ELSE 0 END) AS total_paid
FROM billing
GROUP BY payment_method
ORDER BY payment_method;

-- Example 232
-- Question: Which three branches have the largest billed totals? Use branch name to break ties.
SELECT d.hospital_branch, SUM(b.amount) AS total_billed
FROM billing AS b
JOIN treatments AS t ON b.treatment_id = t.treatment_id
JOIN appointments AS a ON t.appointment_id = a.appointment_id
JOIN doctors AS d ON a.doctor_id = d.doctor_id
GROUP BY d.hospital_branch
ORDER BY total_billed DESC, d.hospital_branch
LIMIT 3;

-- Example 233
-- Question: List only appointments with a bill above 1,000, showing each appointment once even if several bills qualify.
SELECT DISTINCT a.appointment_id, a.appointment_date, a.patient_id, a.doctor_id
FROM appointments AS a
JOIN treatments AS t ON t.appointment_id = a.appointment_id
JOIN billing AS b ON b.treatment_id = t.treatment_id
WHERE b.amount > 1000
ORDER BY a.appointment_date, a.appointment_id;

-- Example 234
-- Question: How many distinct appointments have at least one bill currently marked Paid?
SELECT COUNT(DISTINCT a.appointment_id) AS appointment_count
FROM appointments AS a
JOIN treatments AS t ON t.appointment_id = a.appointment_id
JOIN billing AS b ON b.treatment_id = t.treatment_id
WHERE b.payment_status = 'Paid';

-- Example 235
-- Question: Which appointments have at least one Paid bill and at least one Pending bill?
SELECT a.appointment_id, a.appointment_date
FROM appointments AS a
JOIN treatments AS t ON t.appointment_id = a.appointment_id
JOIN billing AS b ON b.treatment_id = t.treatment_id
GROUP BY a.appointment_id, a.appointment_date
HAVING SUM(CASE WHEN b.payment_status = 'Paid' THEN 1 ELSE 0 END) > 0
   AND SUM(CASE WHEN b.payment_status = 'Pending' THEN 1 ELSE 0 END) > 0
ORDER BY a.appointment_date, a.appointment_id;

-- Example 236
-- Question: Which appointments have bills and all of those bills are marked Paid?
SELECT a.appointment_id, a.appointment_date, COUNT(b.bill_id) AS bill_count
FROM appointments AS a
JOIN treatments AS t ON t.appointment_id = a.appointment_id
JOIN billing AS b ON b.treatment_id = t.treatment_id
GROUP BY a.appointment_id, a.appointment_date
HAVING COUNT(b.bill_id) > 0
   AND SUM(CASE WHEN b.payment_status <> 'Paid' THEN 1 ELSE 0 END) = 0
ORDER BY a.appointment_date, a.appointment_id;

-- Example 237
-- Question: For every patient, count Completed appointments during May 2023, including zero.
SELECT p.patient_id, p.first_name, p.last_name,
       COUNT(a.appointment_id) AS completed_count
FROM patients AS p
LEFT JOIN appointments AS a
    ON a.patient_id = p.patient_id
   AND a.status = 'Completed'
   AND a.appointment_date >= '2023-05-01'
   AND a.appointment_date < '2023-06-01'
GROUP BY p.patient_id, p.first_name, p.last_name
ORDER BY p.patient_id;

-- Example 238
-- Question: Which doctors had no Completed appointments during May 2023, including doctors with no appointments at all?
SELECT d.doctor_id, d.first_name, d.last_name
FROM doctors AS d
LEFT JOIN appointments AS a
    ON a.doctor_id = d.doctor_id
   AND a.status = 'Completed'
   AND a.appointment_date >= '2023-05-01'
   AND a.appointment_date < '2023-06-01'
WHERE a.appointment_id IS NULL
ORDER BY d.doctor_id;

-- Example 239
-- Question: List all appointments for patient P005, with doctor name and any treatments; keep appointments without treatments.
SELECT a.appointment_id, a.appointment_date,
       d.doctor_id, d.first_name, d.last_name,
       t.treatment_id, t.treatment_type
FROM appointments AS a
JOIN doctors AS d ON a.doctor_id = d.doctor_id
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
WHERE a.patient_id = 'P005'
ORDER BY a.appointment_date, a.appointment_id, t.treatment_id;

-- Example 240
-- Question: Show every appointment for doctor D009 with its number of treatments and total treatment cost, leaving the cost NULL when there are no treatments.
SELECT a.appointment_id, a.appointment_date,
       COUNT(t.treatment_id) AS treatment_count,
       SUM(t.cost) AS total_treatment_cost
FROM appointments AS a
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
WHERE a.doctor_id = 'D009'
GROUP BY a.appointment_id, a.appointment_date
ORDER BY a.appointment_date, a.appointment_id;

-- Example 241
-- Question: List all doctors whose first name is Jane, including their IDs, full names, specializations and branches.
SELECT doctor_id, first_name, last_name, specialization, hospital_branch
FROM doctors
WHERE first_name = 'Jane'
ORDER BY last_name, doctor_id;

-- Example 242
-- Question: List all patients whose first name is David, including their IDs and dates of birth to distinguish them.
SELECT patient_id, first_name, last_name, date_of_birth
FROM patients
WHERE first_name = 'David'
ORDER BY last_name, date_of_birth, patient_id;

-- Example 243
-- Question: List all doctor records whose full name is David Taylor; do not choose one record if more than one matches.
-- Dr. is an honorific and must not become a first-name or last-name value.
SELECT doctor_id, first_name, last_name, specialization, hospital_branch
FROM doctors
WHERE first_name = 'David'
  AND last_name = 'Taylor'
ORDER BY doctor_id;

-- Example 244
-- Question: For every doctor, show appointment counts for June and July 2023 in separate columns, including zero.
SELECT d.doctor_id, d.first_name, d.last_name,
       COUNT(CASE
                 WHEN a.appointment_date >= '2023-06-01'
                  AND a.appointment_date < '2023-07-01' THEN a.appointment_id
             END) AS june_count,
       COUNT(CASE
                 WHEN a.appointment_date >= '2023-07-01'
                  AND a.appointment_date < '2023-08-01' THEN a.appointment_id
             END) AS july_count
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
GROUP BY d.doctor_id, d.first_name, d.last_name
ORDER BY d.doctor_id;

-- Example 245
-- Question: Which doctors had more appointments in July 2023 than in June 2023?
SELECT d.doctor_id, d.first_name, d.last_name,
       COUNT(CASE
                 WHEN a.appointment_date >= '2023-06-01'
                  AND a.appointment_date < '2023-07-01' THEN a.appointment_id
             END) AS june_count,
       COUNT(CASE
                 WHEN a.appointment_date >= '2023-07-01'
                  AND a.appointment_date < '2023-08-01' THEN a.appointment_id
             END) AS july_count
FROM doctors AS d
LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id
GROUP BY d.doctor_id, d.first_name, d.last_name
HAVING COUNT(CASE
                 WHEN a.appointment_date >= '2023-07-01'
                  AND a.appointment_date < '2023-08-01' THEN a.appointment_id
             END)
     > COUNT(CASE
                 WHEN a.appointment_date >= '2023-06-01'
                  AND a.appointment_date < '2023-07-01' THEN a.appointment_id
             END)
ORDER BY d.doctor_id;

-- Example 246
-- Question: Show the average age at Completed appointments for each specialization, weighting each appointment equally rather than each unique patient.
SELECT d.specialization,
       ROUND(AVG(TIMESTAMPDIFF(YEAR, p.date_of_birth, a.appointment_date)), 1) AS average_age_at_appointment
FROM appointments AS a
JOIN patients AS p ON a.patient_id = p.patient_id
JOIN doctors AS d ON a.doctor_id = d.doctor_id
WHERE a.status = 'Completed'
GROUP BY d.specialization
ORDER BY d.specialization;

-- Example 247
-- Question: List every appointment with any linked invoice, distinguishing no invoice from an existing zero-amount invoice.
SELECT a.appointment_id, t.treatment_id, b.bill_id, b.amount AS invoice_amount,
       CASE
           WHEN b.bill_id IS NULL THEN 'No invoice'
           WHEN b.amount = 0 THEN 'Zero-amount invoice'
           ELSE 'Invoice recorded'
       END AS invoice_state
FROM appointments AS a
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
ORDER BY a.appointment_id, t.treatment_id, b.bill_id;

-- Example 248
-- Question: Show every appointment with any invoices issued in November 2023, retaining appointments without a matching invoice.
SELECT a.appointment_id, a.appointment_date,
       t.treatment_id, b.bill_id, b.bill_date, b.amount
FROM appointments AS a
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b
    ON b.treatment_id = t.treatment_id
   AND b.bill_date >= '2023-11-01'
   AND b.bill_date < '2023-12-01'
ORDER BY a.appointment_id, t.treatment_id, b.bill_id;

-- Example 249
-- Question: Show only appointments with invoices issued in November 2023, with one row per matching invoice.
SELECT a.appointment_id, a.appointment_date,
       b.bill_id, b.bill_date, b.amount
FROM appointments AS a
JOIN treatments AS t ON t.appointment_id = a.appointment_id
JOIN billing AS b ON b.treatment_id = t.treatment_id
WHERE b.bill_date >= '2023-11-01'
  AND b.bill_date < '2023-12-01'
ORDER BY a.appointment_id, b.bill_id;

-- Example 250
-- Question: For every appointment, show its earliest and latest bill issue dates and bill count, retaining appointments without bills.
SELECT a.appointment_id, a.appointment_date,
       COUNT(b.bill_id) AS bill_count,
       MIN(b.bill_date) AS first_bill_date,
       MAX(b.bill_date) AS latest_bill_date
FROM appointments AS a
LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id
LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id
GROUP BY a.appointment_id, a.appointment_date
ORDER BY a.appointment_date, a.appointment_id;