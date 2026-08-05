/*M!999999\- enable the sandbox mode */ 
-- MariaDB dump 10.19-11.7.2-MariaDB, for debian-linux-gnu (x86_64)
--
-- Host: localhost    Database: tradingSignals_rehearsal
-- ------------------------------------------------------
-- Server version	11.7.2-MariaDB-ubu2404

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*M!100616 SET @OLD_NOTE_VERBOSITY=@@NOTE_VERBOSITY, NOTE_VERBOSITY=0 */;

--
-- Table structure for table `instruments`
--

DROP TABLE IF EXISTS `instruments`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `instruments` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `symbol` varchar(20) NOT NULL,
  `mt5_symbol` varchar(20) DEFAULT NULL,
  `name` varchar(100) NOT NULL,
  `category` enum('precious_metals','base_metals','forex_major','forex_minor','indices','crypto') NOT NULL,
  `pip_value_per_lot` decimal(10,4) NOT NULL,
  `contract_size` int(11) NOT NULL DEFAULT 1,
  `default_sl_distance` decimal(10,4) NOT NULL,
  `default_tp_multiplier` decimal(5,2) DEFAULT 1.50,
  `min_lot_size` decimal(10,4) DEFAULT 0.0100,
  `max_lot_size` decimal(10,4) DEFAULT 100.0000,
  `trading_hours_start` time DEFAULT '00:00:00',
  `trading_hours_end` time DEFAULT '23:59:59',
  `enabled` tinyint(1) DEFAULT 1,
  `oanda_compatible` tinyint(1) DEFAULT 1,
  `ibkr_compatible` tinyint(1) DEFAULT 0,
  `created_at` timestamp NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `idx_symbol` (`symbol`)
) ENGINE=InnoDB AUTO_INCREMENT=15 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `instruments`
--

LOCK TABLES `instruments` WRITE;
/*!40000 ALTER TABLE `instruments` DISABLE KEYS */;
INSERT INTO `instruments` VALUES
(1,'XAU_USD',NULL,'Gold vs US Dollar','precious_metals',1.0000,1,5.0000,1.50,0.0100,100.0000,'00:00:00','23:59:59',1,1,0,'2026-08-05 16:29:47'),
(2,'XAG_USD',NULL,'Silver vs US Dollar','precious_metals',1.0000,1,0.2000,1.50,0.0100,100.0000,'00:00:00','23:59:59',1,1,0,'2026-08-05 16:29:47'),
(3,'XPT_USD',NULL,'Platinum vs US Dollar','precious_metals',1.0000,1,5.0000,1.50,0.0100,100.0000,'00:00:00','23:59:59',1,1,0,'2026-08-05 16:29:47'),
(4,'XCU_USD',NULL,'Copper vs US Dollar','base_metals',1.0000,1,0.1000,1.50,0.0100,100.0000,'00:00:00','23:59:59',1,1,0,'2026-08-05 16:29:47'),
(5,'EUR_USD',NULL,'Euro vs US Dollar','forex_major',1.0000,100000,0.0050,1.50,0.0100,100.0000,'00:00:00','23:59:59',1,1,0,'2026-08-05 16:29:47'),
(6,'GBP_USD',NULL,'Pound vs US Dollar','forex_major',1.0000,100000,0.0050,1.50,0.0100,100.0000,'00:00:00','23:59:59',1,1,0,'2026-08-05 16:29:47'),
(7,'USD_JPY',NULL,'US Dollar vs Yen','forex_major',1.0000,100000,0.5000,1.50,0.0100,100.0000,'00:00:00','23:59:59',1,1,0,'2026-08-05 16:29:47'),
(8,'USD_CHF',NULL,'US Dollar vs Swiss Franc','forex_major',1.0000,100000,0.0050,1.50,0.0100,100.0000,'00:00:00','23:59:59',1,1,0,'2026-08-05 16:29:47'),
(9,'USD_CAD',NULL,'US Dollar vs Canadian Dollar','forex_major',1.0000,100000,0.0050,1.50,0.0100,100.0000,'00:00:00','23:59:59',1,1,0,'2026-08-05 16:29:47'),
(10,'AUD_USD',NULL,'Aussie vs US Dollar','forex_major',1.0000,100000,0.0050,1.50,0.0100,100.0000,'00:00:00','23:59:59',1,1,0,'2026-08-05 16:29:47'),
(11,'NZD_USD',NULL,'Kiwi vs US Dollar','forex_major',1.0000,100000,0.0050,1.50,0.0100,100.0000,'00:00:00','23:59:59',1,1,0,'2026-08-05 16:29:47'),
(12,'EUR_GBP',NULL,'Euro vs Pound','forex_minor',1.0000,100000,0.0050,1.50,0.0100,100.0000,'00:00:00','23:59:59',1,1,0,'2026-08-05 16:29:47'),
(13,'SPX500_USD',NULL,'S&P 500 Index vs US Dollar','indices',1.0000,1,25.0000,1.50,0.0100,100.0000,'00:00:00','23:59:59',1,1,0,'2026-08-05 16:29:47'),
(14,'WTICO_USD',NULL,'WTI Crude Oil vs US Dollar','base_metals',10.0000,1000,0.5000,1.50,0.0100,100.0000,'00:00:00','23:59:59',1,1,0,'2026-08-05 16:29:47');
/*!40000 ALTER TABLE `instruments` ENABLE KEYS */;
UNLOCK TABLES;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*M!100616 SET NOTE_VERBOSITY=@OLD_NOTE_VERBOSITY */;

-- Dump completed on 2026-08-05 16:29:47
