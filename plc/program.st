(*
   IPMS — conduite de la production d'eau glacée des baies radar.

   C'est le programme qui rend le lab intéressant : il ne pilote pas le CMS,
   il pilote la plateforme. Et l'état de la plateforme conditionne ce que le
   CMS peut voir — température haute, émission déclassée, horizon de
   détection réduit.

   Adressage. Les points distants (capteurs interrogés par OpenPLC en tant
   que maître) sont mappés à partir de l'adresse 100. Les %Q sont l'image
   transmise au CMS par le serveur Modbus de l'automate. Vérifier le mapping
   réellement attribué dans l'interface web, page Slave Devices : c'est elle
   qui fait foi, pas ce commentaire.

   Voir modbus-map.md.
*)

PROGRAM ipms
  VAR
    (* --- entrées terrain, lues par l'automate maître --- *)
    temp_brute   AT %IW100  : INT;     (* dixièmes de °C   *)
    press_brute  AT %IW101  : INT;     (* centibars        *)
    rpm_brute    AT %IW102  : INT;     (* dixièmes de tr/min *)
    rot_antenne  AT %IX100.0 : BOOL;
    pompe_marche AT %IX100.1 : BOOL;

    (* --- image transmise au CMS --- *)
    out_temp     AT %QW0    : INT;
    out_press    AT %QW1    : INT;
    out_rpm      AT %QW2    : INT;
    out_puiss    AT %QW3    : INT;     (* dixièmes de %    *)
    cms_rot      AT %QX0.0  : BOOL;
    cms_pompe    AT %QX0.1  : BOOL;
    cms_al_temp  AT %QX0.2  : BOOL;
    cms_al_trip  AT %QX0.3  : BOOL;

    (* --- interne --- *)
    seuil_haut   : BOOL;
    tempo_alarme : TON;
    puiss_calc   : REAL;
  END_VAR

  (* Hystérésis : on arme l'alarme à 70 °C, on ne la désarme qu'à 66 °C.
     Sans cela, une valeur qui oscille autour du seuil fait clignoter
     l'alarme et l'opérateur cesse de la regarder. *)
  IF temp_brute >= 700 THEN
    seuil_haut := TRUE;
  ELSIF temp_brute <= 660 THEN
    seuil_haut := FALSE;
  END_IF;

  (* Temporisation anti-rebond : un pic de trois secondes sur un capteur
     n'est pas une avarie de refroidissement. *)
  tempo_alarme(IN := seuil_haut, PT := T#5s);
  cms_al_temp := tempo_alarme.Q;

  (* Coupure d'émission. Au-delà de 88 °C on ne déclasse plus, on arrête :
     protéger l'émetteur prime sur la veille, parce qu'un émetteur détruit
     ne revient pas en service à la fin de l'alerte. *)
  cms_al_trip := temp_brute >= 880;

  (* Déclassement progressif entre 70 et 88 °C, en dixièmes de pour cent. *)
  IF cms_al_trip THEN
    puiss_calc := 20.0;
  ELSIF temp_brute <= 700 THEN
    puiss_calc := 1000.0;
  ELSE
    puiss_calc := 1000.0 - 850.0 * (INT_TO_REAL(temp_brute) - 700.0) / 180.0;
  END_IF;

  out_temp  := temp_brute;
  out_press := press_brute;
  out_rpm   := rpm_brute;
  out_puiss := REAL_TO_INT(puiss_calc);
  cms_rot   := rot_antenne;
  cms_pompe := pompe_marche;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK cyclique(INTERVAL := T#50ms, PRIORITY := 0);
    PROGRAM inst0 WITH cyclique : ipms;
  END_RESOURCE
END_CONFIGURATION
