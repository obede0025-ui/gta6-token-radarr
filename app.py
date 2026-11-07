# ============================================================
# V5.7.1 — VEILIGE HERSTELKNOP VOOR STRATEGIE B
# ============================================================

def herstelpaneel_v2():
    st.divider()
    st.subheader("🛟 Historische papertrades herstellen")

    st.caption(
        "Deze functie voegt ontbrekende Strategie B-trades toe. "
        "Bestaande afgesloten resultaten worden niet overschreven."
    )

    if (
        st.session_state.get("v542_auto", True)
        or st.session_state.get("v542_a", True)
        or st.session_state.get("v542_b", True)
    ):
        st.warning(
            "Zet eerst Automatisch scannen en paperinstappen A/B UIT."
        )
        return

    config = storage_config()

    if config is None:
        st.error("Supabase-configuratie ontbreekt.")
        return

    source_name = "radar/gta6_radar_hersteld_v2.db"

    try:
        source_data = get_object(config, source_name)

        if source_data is None:
            st.error(
                "Herstelde database niet gevonden: " + source_name
            )
            return

        stats = inspect_database(source_data)

        if stats["paper_v542_b"] != 4:
            st.error(
                "Herstel geblokkeerd: de bron bevat niet "
                "exact vier Strategie B-trades."
            )
            return

        with tempfile.NamedTemporaryFile(suffix=".db") as temp:
            temp.write(source_data)
            temp.flush()

            with sqlite3.connect(
                "file:" + temp.name + "?mode=ro",
                uri=True
            ) as source:
                columns = [
                    row[1]
                    for row in source.execute(
                        "PRAGMA table_info(paper_v542_b)"
                    )
                ]

                rows = source.execute(
                    "SELECT * FROM paper_v542_b ORDER BY id"
                ).fetchall()

        trades = [
            dict(zip(columns, row))
            for row in rows
        ]

        if any(
            trade["status"] != "CLOSED"
            or trade["pnl_usd"] is None
            for trade in trades
        ):
            st.error(
                "Herstel geblokkeerd: niet alle vier "
                "brontrades hebben een afsluitresultaat."
            )
            return

        keys = [
            (trade["address"], trade["opened_at"])
            for trade in trades
        ]

        if len(keys) != len(set(keys)):
            st.error(
                "Herstel geblokkeerd: dubbele trades "
                "in de herstelkopie."
            )
            return

        with db() as connection:
            current_rows = connection.execute(
                """
                SELECT address,opened_at,status,pnl_usd
                FROM paper_v542_b
                """
            ).fetchall()

        current = {
            (address, opened): (status, pnl)
            for address, opened, status, pnl in current_rows
        }

        missing = 0
        to_complete = 0
        conflicts = []

        for trade in trades:
            key = (trade["address"], trade["opened_at"])

            if key not in current:
                missing += 1
                continue

            status, pnl = current[key]

            if status == "OPEN":
                to_complete += 1

            elif (
                status != "CLOSED"
                or pnl is None
                or abs(float(pnl) - float(trade["pnl_usd"])) > 0.01
            ):
                conflicts.append(trade["name"])

        st.write("**Voorcontrole:**")
        st.write("Herstelbron: 4 afgesloten trades")
        st.write("Ontbrekende trades:", missing)
        st.write("Open trades aan te vullen:", to_complete)
        st.write("Conflicten:", len(conflicts))

        if conflicts:
            st.error(
                "Herstel geblokkeerd door afwijkende "
                "resultaten: " + ", ".join(conflicts)
            )
            return

    except (
        requests.RequestException,
        sqlite3.Error,
        RuntimeError,
        ValueError,
        OSError,
        TypeError
    ) as error:
        st.error("Voorcontrole mislukt: " + str(error))
        return

    confirmed = st.checkbox(
        "Ik wil de ontbrekende historische papertrades herstellen.",
        key="herstel_v2_toestemming"
    )

    if not st.button(
        "🛟 Veiligheidskopie maken en herstellen",
        disabled=not confirmed,
        key="herstel_v2_knop"
    ):
        return

    try:
        with _LOCK:

            # 1. Eerst huidige database veiligstellen
            before = database_snapshot()
            inspect_database(before)

            timestamp = datetime.now(
                timezone.utc
            ).strftime("%Y%m%dT%H%M%SZ")

            identifier = uuid.uuid4().hex[:8]

            backup_name = (
                f"radar/recovery-safety/"
                f"pre_{timestamp}_{identifier}.db"
            )

            upload_object(
                config,
                backup_name,
                before,
                overwrite=False
            )

            # 2. Trades samenvoegen in één transactie
            with db() as connection:
                connection.execute("BEGIN IMMEDIATE")

                target_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(paper_v542_b)"
                    )
                }

                copy_columns = [
                    column
                    for column in columns
                    if column in target_columns
                    and column != "id"
                ]

                inserted = 0
                completed = 0

                for trade in trades:

                    existing = connection.execute(
                        """
                        SELECT id,status,pnl_usd
                        FROM paper_v542_b
                        WHERE address=? AND opened_at=?
                        """,
                        (
                            trade["address"],
                            trade["opened_at"]
                        )
                    ).fetchall()

                    if len(existing) > 1:
                        raise RuntimeError(
                            "Dubbele lokale trade gevonden."
                        )

                    if not existing:

                        names = ",".join(
                            f'"{column}"'
                            for column in copy_columns
                        )

                        placeholders = ",".join(
                            "?" for _ in copy_columns
                        )

                        connection.execute(
                            f"INSERT INTO paper_v542_b "
                            f"({names}) VALUES ({placeholders})",
                            [
                                trade[column]
                                for column in copy_columns
                            ]
                        )

                        inserted += 1

                    elif existing[0][1] == "OPEN":

                        connection.execute(
                            """
                            UPDATE paper_v542_b
                            SET status='CLOSED',
                                pnl_usd=?,
                                exit_reason=?,
                                closed_at=COALESCE(?,closed_at),
                                exit_market=COALESCE(?,exit_market),
                                exit_effective=COALESCE(
                                    ?,exit_effective
                                )
                            WHERE id=? AND status='OPEN'
                            """,
                            (
                                trade["pnl_usd"],
                                trade["exit_reason"],
                                trade["closed_at"],
                                trade["exit_market"],
                                trade["exit_effective"],
                                existing[0][0]
                            )
                        )

                        completed += 1

                    elif (
                        existing[0][1] != "CLOSED"
                        or existing[0][2] is None
                        or abs(
                            float(existing[0][2])
                            - float(trade["pnl_usd"])
                        ) > 0.01
                    ):
                        raise RuntimeError(
                            "Tegenstrijdige trade gevonden. "
                            "Herstel wordt teruggedraaid."
                        )

            # 3. Samengevoegde database apart bewaren
            after = database_snapshot()
            inspect_database(after)

            merged_name = (
                f"radar/recovery-safety/"
                f"merged_{timestamp}_{identifier}.db"
            )

            upload_object(
                config,
                merged_name,
                after,
                overwrite=False
            )

            # 4. Probeer actieve back-up bij te werken
            try:
                backup_status = backup_remote(force=True)

            except (
                requests.RequestException,
                sqlite3.Error,
                RuntimeError,
                ValueError,
                OSError
            ) as error:
                backup_status = (
                    "Actieve back-up niet bijgewerkt: "
                    + str(error)
                )

            st.success(
                f"Herstel uitgevoerd: {inserted} toegevoegd, "
                f"{completed} bijgewerkt."
            )

            st.write("Veiligheidskopie:", backup_name)
            st.write("Samengevoegde kopie:", merged_name)
            st.write("Actieve back-up:", backup_status)

            st.info(
                "Controleer Strategie B. "
                "Herstart Streamlit voorlopig niet."
            )

    except (
        requests.RequestException,
        sqlite3.Error,
        RuntimeError,
        ValueError,
        OSError,
        TypeError
    ) as error:
        st.error("Herstel gestopt: " + str(error))


herstelpaneel_v2()
