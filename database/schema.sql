-- MedLens schema for the Kaggle "Hospital Management Dataset".
-- Re-running this file drops and recreates the tables (data is reloaded by load_data.py).

CREATE DATABASE IF NOT EXISTS medlens
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

USE medlens;

-- Drop children before parents so foreign keys don't block the drop.
DROP TABLE IF EXISTS billing;
DROP TABLE IF EXISTS treatments;
DROP TABLE IF EXISTS appointments;
DROP TABLE IF EXISTS doctors;
DROP TABLE IF EXISTS patients;

CREATE TABLE patients (
  patient_id         VARCHAR(10)   NOT NULL,
  first_name         VARCHAR(50)   NOT NULL,
  last_name          VARCHAR(50)   NOT NULL,
  gender             ENUM('M','F') NOT NULL,
  date_of_birth      DATE          NOT NULL,
  contact_number     VARCHAR(20)   NOT NULL,  -- text, not a number: keeps leading zeros / "+"
  address            VARCHAR(255)  NOT NULL,
  registration_date  DATE          NOT NULL,
  insurance_provider VARCHAR(100)  NULL,
  insurance_number   VARCHAR(50)   NULL,
  email              VARCHAR(255)  NULL,      -- not UNIQUE: the source data has duplicates
  PRIMARY KEY (patient_id),
  UNIQUE KEY uq_patients_insurance_number (insurance_number)
) ENGINE=InnoDB;

CREATE TABLE doctors (
  doctor_id        VARCHAR(10)      NOT NULL,
  first_name       VARCHAR(50)      NOT NULL,
  last_name        VARCHAR(50)      NOT NULL,
  specialization   VARCHAR(100)     NOT NULL,
  phone_number     VARCHAR(20)      NOT NULL,
  years_experience TINYINT UNSIGNED NOT NULL,
  hospital_branch  VARCHAR(100)     NOT NULL,
  email            VARCHAR(255)     NOT NULL,
  PRIMARY KEY (doctor_id),
  UNIQUE KEY uq_doctors_email (email)
) ENGINE=InnoDB;

CREATE TABLE appointments (
  appointment_id   VARCHAR(10)  NOT NULL,
  patient_id       VARCHAR(10)  NOT NULL,
  doctor_id        VARCHAR(10)  NOT NULL,
  appointment_date DATE         NOT NULL,
  appointment_time TIME         NOT NULL,
  reason_for_visit VARCHAR(100) NOT NULL,
  status           ENUM('Scheduled','Completed','Cancelled','No-show') NOT NULL,
  PRIMARY KEY (appointment_id),
  KEY idx_appointments_date (appointment_date),
  CONSTRAINT fk_appointments_patient FOREIGN KEY (patient_id) REFERENCES patients (patient_id),
  CONSTRAINT fk_appointments_doctor  FOREIGN KEY (doctor_id)  REFERENCES doctors (doctor_id)
) ENGINE=InnoDB;

CREATE TABLE treatments (
  treatment_id   VARCHAR(10)   NOT NULL,
  appointment_id VARCHAR(10)   NOT NULL,
  treatment_type VARCHAR(100)  NOT NULL,
  description    VARCHAR(255)  NULL,
  cost           DECIMAL(10,2) NOT NULL,  -- DECIMAL, not FLOAT: exact money values
  treatment_date DATE          NOT NULL,
  PRIMARY KEY (treatment_id),
  CONSTRAINT fk_treatments_appointment FOREIGN KEY (appointment_id) REFERENCES appointments (appointment_id),
  CONSTRAINT chk_treatments_cost CHECK (cost >= 0)
) ENGINE=InnoDB;

CREATE TABLE billing (
  bill_id        VARCHAR(10)   NOT NULL,
  patient_id     VARCHAR(10)   NOT NULL,
  treatment_id   VARCHAR(10)   NOT NULL,
  bill_date      DATE          NOT NULL,
  amount         DECIMAL(10,2) NOT NULL,
  payment_method ENUM('Cash','Credit Card','Insurance') NOT NULL,
  payment_status ENUM('Paid','Pending','Failed')        NOT NULL,
  PRIMARY KEY (bill_id),
  KEY idx_billing_date (bill_date),
  CONSTRAINT fk_billing_patient   FOREIGN KEY (patient_id)   REFERENCES patients (patient_id),
  CONSTRAINT fk_billing_treatment FOREIGN KEY (treatment_id) REFERENCES treatments (treatment_id),
  CONSTRAINT chk_billing_amount CHECK (amount >= 0)
) ENGINE=InnoDB;
